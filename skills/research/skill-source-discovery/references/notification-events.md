# Notification Events

The skill decides *what* is worth notifying and persists it. Hermes delivers it. The
skill hardcodes no Discord/Slack formatting, channel, or webhook (§11.4). Events live
in the `notifications` table; `emit-events` hands pending ones to the agent, which
delivers them through whatever channel the Hermes instance has, then calls `ack-events`.

## Event types (§11.1)

| event_type | fires when | timing |
|---|---|---|
| `new_source.accepted` | a source crosses the acceptance threshold for the first time | immediately |
| `new_source.needs_review` | a promising but ambiguous source appears | batched into the digest, unless high-confidence or found via a high-trust parent |
| `source_status.changed` | an accepted source becomes dead, archived, rejected, or is restored | immediately |
| `scan.error_threshold` | provider/source scan failures exceed the configured limit | immediately |
| `daily_digest.available` | a digest exists (new accepted, review candidates, or notable failures) | once per day when there is content |

## Dedupe keys

`UNIQUE(dedupe_key)` guarantees exactly one event per transition. Build the key
deterministically:

| event | dedupe_key |
|---|---|
| `new_source.accepted` | `accepted:<source_id>` |
| `new_source.needs_review` | `needsreview:<source_id>` |
| `source_status.changed` | `status:<source_id>:<from>-><to>` |
| `scan.error_threshold` | `scanerror:<provider>:<run_id>` |
| `daily_digest.available` | `digest:<YYYY-MM-DD>` |

Re-running discovery re-attempts the same insert; `ON CONFLICT(dedupe_key) DO NOTHING`
makes it a no-op. No event is ever sent twice.

## Payload fields (§11.3)

Every payload is JSON built with `json_object` (cookbook §6) and carries:

- `event_type`
- `dedupe_key`
- `source_id`
- `canonical_url`
- `source_type`
- `platform` / `host`
- `confidence`
- `status`
- `title` — short label (repo name or page title)
- `evidence_summary` — the evidence types and weights behind the decision
- `discovery_method` — how it was found (`github_code_search`, `link_graph`, `seed`, `web_search`)
- `discovered_by` — parent source key or query, when known
- `first_seen_at`
- `review_hint` — pointer to the review queue or report

`scan.error_threshold` and `daily_digest.available` are not tied to one source; their
payloads carry counts and a summary instead of source fields.

## Policy (§11.2)

- Emit `new_source.accepted` immediately, one per source per acceptance.
- Emit at most once per source per status transition.
- Batch `new_source.needs_review` into the digest unless `confidence` is high or the
  parent is a high-trust source.
- Do **not** emit for each new individual skill inside an already-known source unless
  config turns that on.
- Do **not** emit when the source has an active `suppress_notifications` override.
- Never emit duplicates on repeated scans — the dedupe key handles it.

## Delivery boundary

`emit-events` returns a JSON array; the agent delivers each through the instance's
channel and then `ack-events` flips `delivered_at` from NULL to a timestamp. Because
ack only acts on still-NULL rows, Hermes can retry delivery safely — the skill never
regenerates or double-sends. Delivery formatting, channel IDs, retries, and rate limits
belong to Hermes, not here.

## Test event

`emit-test-event` (cookbook-equivalent: insert a `daily_digest.available` row with a
`test:` dedupe prefix) lets Hermes validate routing end to end without waiting for a
real discovery:

```bash
sqlite3 "$DB" "INSERT INTO notifications (dedupe_key,event_type,payload_json)
VALUES ('test:'||strftime('%Y%m%dT%H%M%SZ','now'),'daily_digest.available',
  json_object('event_type','daily_digest.available','note','routing test','accepted_today',0))
ON CONFLICT(dedupe_key) DO NOTHING;"
```
