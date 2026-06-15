# Scoring Policy

Scoring is transparent and reproducible: the agent records typed **evidence**, and
`confidence`/`status` are computed from it by the SQL in `sql-cookbook.md` §4. The
agent never writes a bare confidence number. Weights and thresholds live in config
(`assets/config.example.yaml` → `scoring:`) so they tune without code changes; the
defaults below are the starting point.

## Evidence types and weights

Record one `source_observations` row per signal, with the `weight` from this table
(§7.1, §7.2 of the requirements). Weights are points; positive supports a source,
negative argues against it.

### Positive

| evidence_type | weight | when to record |
|---|---|---|
| `has_skill_md` | +2.0 | a valid `SKILL.md` with recognizable frontmatter exists |
| `multiple_skill_dirs` | +2.0 | two or more skill directories in known paths |
| `skills_in_known_path` | +1.0 | skills under `skills/`, `.agents/skills/`, `.claude/skills/`, `.github/skills/` |
| `readme_describes_skills` | +1.0 | README/site text explicitly describes agent skills or a collection |
| `topic_match` | +1.0 | repo topics/tags like `agent-skills`, `claude-skills`, `codex-skills` |
| `directory_lists_multiple` | +2.0 | catalog/directory structure listing multiple skills or repos |
| `linked_by_accepted_high_trust` | +1.5 | linked from an already-accepted official/high-trust source |
| `has_license` | +0.5 | a public license or clear distribution terms |
| `recently_maintained` | +0.5 | commit/update activity within the last ~6 months |

### Negative

| evidence_type | weight | when to record |
|---|---|---|
| `no_public_access` | −5.0 | not publicly accessible (hard signal → rejected) |
| `spam_or_linkfarm` | −5.0 | spam metadata, link-farm behavior, obfuscated structure (hard signal → rejected) |
| `archived_or_deleted` | −2.0 | archived, deleted, or empty repo |
| `unreachable` | −2.0 | fetch failed this scan (also bump `fail_count`) |
| `single_unrelated_mention` | −2.0 | one unrelated occurrence of "skill"; no packaging |
| `marketing_only` | −2.0 | product marketing page with no source inventory |
| `prompts_no_packaging` | −1.5 | prompt collection, no skill packaging and no source links |
| `duplicate_mirror_no_value` | −1.0 | mirror with no independent inventory value (record a `mirrors` edge too) |

## Confidence

```
confidence = clamp( sum(weights) / saturation , 0.0 , 1.0 )
```

Default `saturation = 5.0` — roughly "one strong repo's worth of positive evidence
reaches full confidence." Stored rounded to 3 decimals.

## Status

Computed in priority order (cookbook §4b). A source **auto-accepts** once it crosses
`accept` with at least one positive evidence item, regardless of how it was discovered
(the resolved §22.2 decision — discovery path does not gate acceptance).

| order | rule | status |
|---|---|---|
| 1 | active manual `status` override exists | that value |
| 2 | any `spam_or_linkfarm` evidence | `rejected` |
| 3 | `fail_count >= dead_after` | `dead` |
| 4 | any `no_public_access` evidence | `rejected` |
| 5 | `confidence >= accept` AND ≥1 positive evidence | `accepted` |
| 6 | `confidence >= review` | `needs_review` |
| 7 | `confidence > 0` | `candidate` |
| 8 | otherwise | `rejected` |

Defaults: `accept = 0.6`, `review = 0.3`, `dead_after = 3` consecutive failed scans.

## Manual overrides

Reviewer decisions live in `manual_overrides` and win over computed status while
`active = 1` (cookbook §10). Rescoring never erases them. Clearing an override
(`active = 0`) lets automated scoring resume on the next recompute.

## Notes

- Popularity is not trust: stars/forks carry **no** weight (§4). They may inform a
  human reviewer but never move the score.
- A lone `SKILL.md` string with no other evidence stays a `candidate` — `has_skill_md`
  alone is +2.0, below `accept × saturation = 3.0` points.
- Tune by editing `scoring:` in config. Because weights are stored per observation,
  a re-run recomputes cleanly; historical evidence rows keep the weight in effect when
  recorded, so change the config then recompute to apply new weights.
