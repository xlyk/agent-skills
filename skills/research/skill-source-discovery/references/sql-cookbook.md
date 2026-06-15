# SQL Cookbook

Every database operation the skill needs, as a snippet you run with the `sqlite3`
CLI. The schema's `UNIQUE` constraints and `ON CONFLICT` clauses enforce dedup and
exactly-one-event, so these snippets are safe to re-run.

**Conventions**

- `$DB` is the inventory path from config (default `${HERMES_DATA_DIR:-$HOME/.hermes}/skill-source-discovery/inventory.db`).
- Every connection must enable foreign keys. Each snippet that writes is shown with `PRAGMA foreign_keys = ON;` — keep it.
- Substitute `<<placeholder>>` values. Quote strings; escape with `jq -r @sh` or `printf %q` when a value may contain quotes.
- Thresholds in angle brackets (`<<accept>>`, `<<review>>`, `<<dead_after>>`, `<<saturation>>`) come from config — see `scoring-policy.md`.

---

## 0. Initialize / migrate

```bash
# create or upgrade the database (idempotent: schema.sql uses IF NOT EXISTS).
# expect a single line `wal` on stdout — that is journal_mode confirming, not an error.
sqlite3 "$DB" < schema.sql

# what version is applied?
sqlite3 "$DB" "SELECT version, name, applied_at FROM schema_migrations ORDER BY version;"
```

Back up before applying a NEW numbered migration (§16):

```bash
cp "$DB" "$DB.bak.$(date +%Y%m%dT%H%M%SZ)"
sqlite3 "$DB" < migrations/0002_whatever.sql   # each new migration records its own row
```

---

## 1. Open a run / scan record

```bash
RUN_ID="$(date +%Y%m%dT%H%M%SZ)-$$"
sqlite3 "$DB" "PRAGMA foreign_keys=ON;
INSERT INTO scans (run_id, command, provider, started_at)
VALUES ('$RUN_ID', '<<discover|scan|ingest>>', '<<github|website|...>>', strftime('%Y-%m-%dT%H:%M:%SZ','now'));"
```

Close it with stats (counts collected during the run):

```bash
sqlite3 "$DB" "UPDATE scans SET finished_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'),
  status='ok',
  stats_json=json_object('scanned',<<n>>,'candidates',<<n>>,'accepted',<<n>>,'dupes_suppressed',<<n>>,'events',<<n>>)
WHERE run_id='$RUN_ID' AND command='<<...>>';"
```

---

## 2. Normalize, then upsert a source

Always normalize first — never hand-write a `canonical_key`.

```bash
N="$(python3 scripts/normalize.py 'https://github.com/Anthropics/Skills')"
KEY=$(jq -r .canonical_key <<<"$N")     # github.com/anthropics/skills
URL=$(jq -r .canonical_url <<<"$N")
PLATFORM=$(jq -r .platform <<<"$N")
HOST=$(jq -r .host <<<"$N")
OWNER=$(jq -r '.owner // ""' <<<"$N")
REPO=$(jq -r '.repo // ""' <<<"$N")     # normalize.py's field is `repo`; the column is `repo_name`

sqlite3 "$DB" "PRAGMA foreign_keys=ON;
INSERT INTO sources (canonical_key, canonical_url, platform, host, owner, repo_name,
                     source_type, discovered_by, discovery_method, last_seen_at, last_checked_at)
VALUES ('$KEY','$URL','$PLATFORM','$HOST','$OWNER','$REPO',
        '<<source_type>>','<<parent_key_or_query>>','<<github_code_search|link_graph|seed|web_search>>',
        strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now'))
ON CONFLICT(canonical_key) DO UPDATE SET
  last_seen_at=excluded.last_seen_at,
  last_checked_at=excluded.last_checked_at,
  canonical_url=excluded.canonical_url,
  title=COALESCE(excluded.title, sources.title),
  updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now');"
```

Get the id back:

```bash
SID=$(sqlite3 "$DB" "SELECT id FROM sources WHERE canonical_key='$KEY';")
```

Record the alias forms (provenance + a second dedup net). `normalize.py` returns the ssh form for git repos:

```bash
# normalize.py emits only ssh-form aliases today, so kind='ssh' is correct here. If a playbook
# records a redirect/canonical/website alias through this snippet, set kind to match the form.
for A in $(jq -r '.aliases[]' <<<"$N"); do
  sqlite3 "$DB" "PRAGMA foreign_keys=ON;
  INSERT INTO source_aliases (source_id, normalized_alias, alias_url, kind)
  VALUES ($SID, '$A', '$A', 'ssh') ON CONFLICT(normalized_alias) DO NOTHING;"
done
```

---

## 3. Record evidence (drives the score)

One row per signal per source (`UNIQUE(source_id, evidence_type)` keeps re-scans idempotent).
`weight` comes from `scoring-policy.md` — positive for support, negative against.

```bash
sqlite3 "$DB" "PRAGMA foreign_keys=ON;
INSERT INTO source_observations (source_id, scan_id, evidence_type, weight, detail)
VALUES ($SID, <<scan_id_or_NULL>>, '<<has_skill_md|topic_match|...>>', <<weight>>, '<<short note/path/count>>')
ON CONFLICT(source_id, evidence_type) DO UPDATE SET
  weight=excluded.weight, detail=excluded.detail, scan_id=excluded.scan_id,
  observed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now');"
```

`<<scan_id_or_NULL>>` is the id of the run record from §1 — create it first, since the foreign
key requires it to exist — or `NULL` if you are not tracking a scan. Re-observing the same signal
updates the row in place, so a re-scan never double-counts. Never write a bare confidence number —
record evidence and recompute (§4).

---

## 4. Recompute confidence and status

Two statements. Run after ingesting evidence for a source. Honors active manual overrides.

```bash
# 4a. confidence = clamp(sum(weight) / saturation, 0, 1)
sqlite3 "$DB" "UPDATE sources SET
  confidence = ROUND(MIN(1.0, MAX(0.0,
    COALESCE((SELECT SUM(weight) FROM source_observations o WHERE o.source_id=sources.id),0) / <<saturation>>)),3),
  updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
WHERE id=$SID;"

# 4b. status from confidence + hard signals; a manual 'status' override wins
sqlite3 "$DB" "UPDATE sources SET status = COALESCE(
  (SELECT value FROM manual_overrides m WHERE m.source_id=sources.id AND m.field='status' AND m.active=1),
  CASE
    WHEN EXISTS (SELECT 1 FROM source_observations o WHERE o.source_id=sources.id AND o.evidence_type='spam_or_linkfarm') THEN 'rejected'
    WHEN sources.fail_count >= <<dead_after>> THEN 'dead'
    WHEN EXISTS (SELECT 1 FROM source_observations o WHERE o.source_id=sources.id AND o.evidence_type='no_public_access') THEN 'rejected'
    WHEN sources.confidence >= <<accept>>
         AND EXISTS (SELECT 1 FROM source_observations o WHERE o.source_id=sources.id AND o.weight > 0) THEN 'accepted'
    WHEN sources.confidence >= <<review>> THEN 'needs_review'
    WHEN sources.confidence > 0 THEN 'candidate'
    ELSE 'rejected'
  END)
WHERE id=$SID;"
```

To fire events on a transition, capture status before 4a and compare after (see §6).

---

## 5. Skill candidates and relationships

```bash
# a detected skill inside a source (secondary dedup via source_id+path, and skill_md_hash across sources)
sqlite3 "$DB" "PRAGMA foreign_keys=ON;
INSERT INTO skill_candidates (source_id, normalized_path, skill_name, skill_md_hash)
VALUES ($SID, '<<skills/foo/SKILL.md>>', '<<name>>', '<<sha256-of-frontmatter>>')
ON CONFLICT(source_id, normalized_path) DO UPDATE SET
  skill_name=excluded.skill_name, skill_md_hash=excluded.skill_md_hash;"

# a typed edge to another source (target may not be ingested yet -> target_id NULL)
sqlite3 "$DB" "PRAGMA foreign_keys=ON;
INSERT INTO source_relationships (source_id, target_url, type)
VALUES ($SID, '<<https://target>>', '<<links_to|indexes|mirrors|forks|references|homepage_for|package_for|same_as>>')
ON CONFLICT(source_id, type, target_url) DO NOTHING;"

# later, resolve edges whose target has since been ingested
sqlite3 "$DB" "UPDATE source_relationships SET target_id=(SELECT id FROM sources WHERE canonical_url=source_relationships.target_url)
WHERE target_id IS NULL;"
```

---

## 6. Stage events (the outbox)

Insert one row per transition. `dedupe_key` + `UNIQUE` make re-emission a no-op. Build the payload with `json_object` so all §11.3 fields travel together. See `notification-events.md` for the full schema.

```bash
# newly accepted
sqlite3 "$DB" "PRAGMA foreign_keys=ON;
INSERT INTO notifications (dedupe_key, event_type, source_id, payload_json)
SELECT 'accepted:'||s.id, 'new_source.accepted', s.id,
  json_object('event_type','new_source.accepted','dedupe_key','accepted:'||s.id,
    'source_id',s.id,'canonical_url',s.canonical_url,'source_type',s.source_type,
    'platform',s.platform,'host',s.host,'confidence',s.confidence,'status',s.status,
    'title',COALESCE(s.title,s.canonical_url),
    'evidence_summary',(SELECT group_concat(evidence_type||'('||weight||')',', ') FROM source_observations o WHERE o.source_id=s.id),
    'discovery_method',s.discovery_method,'discovered_by',s.discovered_by,
    'first_seen_at',s.first_seen_at,'review_hint','review queue: status=needs_review')
FROM sources s WHERE s.id=$SID AND s.status='accepted'
ON CONFLICT(dedupe_key) DO NOTHING;"

# stamp last_changed/last_notified when a transition produced a row
sqlite3 "$DB" "UPDATE sources SET last_changed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'),
  last_notified_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=$SID;"
```

`new_source.needs_review` and `source_status.changed` follow the same shape with their own `event_type` and `dedupe_key` (`needsreview:<id>`, `status:<id>:<from>-><to>`). `needs_review` is usually batched into the digest — see `notification-events.md` §policy.

---

## 7. Emit and acknowledge (Hermes delivers; this never duplicates)

```bash
# emit-events: pending events with their ids, so you ack exactly what you delivered
sqlite3 "$DB" -json "SELECT id, payload_json FROM v_pending_events;"

# ack-events: AFTER Hermes confirms send. ack only flips NULL->timestamp, so retries are safe.
# Verify with the SELECT in the SAME invocation — changes() is unreliable here because each
# `sqlite3` call is a separate process and reports 0 rows changed even on success.
sqlite3 "$DB" "UPDATE notifications SET delivered_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
  WHERE id IN (<<comma,ids>>) AND delivered_at IS NULL;
SELECT 'delivered='||COUNT(*) FROM notifications WHERE id IN (<<comma,ids>>) AND delivered_at IS NOT NULL;"
```

---

## 8. Plan a cycle: what to run

```bash
# discover: enabled queries whose cadence is due (last_run older than the window, or never run)
sqlite3 "$DB" -json "SELECT id, platform, purpose, template, cursor FROM search_queries
WHERE enabled=1 AND (last_run_at IS NULL OR last_run_at < <<cutoff_iso>>) ORDER BY platform;"

# mirror a config query into the DB (run once at startup; idempotent)
sqlite3 "$DB" "INSERT INTO search_queries (platform, purpose, template, cadence)
VALUES ('<<platform>>','<<purpose>>','<<template>>','<<daily|weekly>>')
ON CONFLICT(platform, template) DO UPDATE SET purpose=excluded.purpose, cadence=excluded.cadence, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now');"

# after running a query: advance its cursor and yield
sqlite3 "$DB" "UPDATE search_queries SET last_run_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'),
  cursor='<<next_cursor>>', yield_count=yield_count+<<new_sources>> WHERE id=<<qid>>;"

# scan: accepted sources due for a re-check (cadence + skip recently checked)
sqlite3 "$DB" -json "SELECT id, canonical_url, platform, latest_commit_sha, content_hash, last_checked_at
FROM sources WHERE status IN ('accepted','needs_review')
  AND (last_checked_at IS NULL OR last_checked_at < <<cutoff_iso>>) ORDER BY last_checked_at;"
```

Incremental skip: if the source's `latest_commit_sha` / `content_hash` is unchanged since `last_checked_at`, only touch `last_checked_at` — no re-ingest, no events (§6.6).

Dead detection: on an unreachable check, `UPDATE sources SET fail_count=fail_count+1`; on success, reset to 0. Step 4b flips to `dead` past `<<dead_after>>`.

---

## 9. Reports (§14)

```bash
sqlite3 "$DB" -box  "SELECT * FROM v_inventory_summary;"                      # accepted/candidate/... by platform+type
sqlite3 "$DB" -box  "SELECT * FROM v_review_queue;"                           # candidates needing review
sqlite3 "$DB" -box  "SELECT id,canonical_url,status,confidence FROM sources WHERE status IN ('rejected','dead');"
sqlite3 "$DB" -box  "SELECT * FROM v_dedupe_groups;"                          # alias-bearing (git) sources; websites dedupe by canonical_key
sqlite3 "$DB" -box  "SELECT platform,purpose,template,yield_count FROM search_queries ORDER BY yield_count DESC LIMIT 20;"
sqlite3 "$DB" -box  "SELECT run_id,provider,error FROM scans WHERE status='error' ORDER BY started_at DESC LIMIT 20;"
sqlite3 "$DB" -box  "SELECT id,canonical_url,status,last_changed_at FROM sources WHERE last_changed_at >= '<<since_iso>>' ORDER BY last_changed_at DESC;"
sqlite3 "$DB" -box  "SELECT id,canonical_url,status,confidence,first_seen_at FROM sources WHERE first_seen_at >= '<<since_iso>>';"
```

Exports:

```bash
sqlite3 "$DB" -json "SELECT * FROM sources;"                 > export.json
sqlite3 "$DB" -csv -header "SELECT * FROM sources;"          > export.csv
# markdown: pipe -box or build a table from -json with jq
```

---

## 10. Review actions (§17) — auditable, survive rescoring

```bash
# accept / reject / set type / suppress notifications / mark duplicate: each is a manual_overrides row
sqlite3 "$DB" "PRAGMA foreign_keys=ON;
INSERT INTO manual_overrides (source_id, field, value, reason, reviewer)
VALUES (<<sid>>, 'status', 'accepted', '<<why>>', '<<who>>')
ON CONFLICT(source_id, field) DO UPDATE SET value=excluded.value, reason=excluded.reason, reviewer=excluded.reviewer, active=1, created_at=strftime('%Y-%m-%dT%H:%M:%SZ','now');"

# apply the override immediately
sqlite3 "$DB" "UPDATE sources SET status='accepted' WHERE id=<<sid>>;"

# mark a duplicate / mirror relationship
sqlite3 "$DB" "INSERT INTO source_relationships (source_id, target_url, type)
VALUES (<<dup_sid>>, '<<canonical_url_of_primary>>', 'same_as') ON CONFLICT(source_id,type,target_url) DO NOTHING;"

# suppress future notifications for a source
sqlite3 "$DB" "INSERT INTO manual_overrides (source_id, field, value, reason, reviewer)
VALUES (<<sid>>, 'suppress_notifications', '1', '<<why>>', '<<who>>')
ON CONFLICT(source_id, field) DO UPDATE SET value='1', active=1;"

# requeue for scan
sqlite3 "$DB" "UPDATE sources SET last_checked_at=NULL WHERE id=<<sid>>;"

# clear an override so automated scoring resumes
sqlite3 "$DB" "UPDATE manual_overrides SET active=0 WHERE source_id=<<sid>> AND field='<<field>>';"
```

Skip staging an event when `suppress_notifications` is active:

```sql
... AND NOT EXISTS (SELECT 1 FROM manual_overrides m
  WHERE m.source_id=s.id AND m.field='suppress_notifications' AND m.value='1' AND m.active=1)
```
