# AGENTS.md — wiki conventions

This file is the schema. `ingest.py`, `query.py`, and `lint.py` all read it
(as a system prompt fragment) so behavior stays consistent across runs and
across model swaps. Edit this file, not the prompts embedded in the scripts,
when you want to change how the wiki organizes itself.

Status: **draft v1** — expect this to change as we use the system and see
what the deep model actually produces.

## The three layers

- **`raw/`** — immutable source documents. Written once at ingest time,
  never edited or deleted afterward. If a source turns out to be wrong,
  correct the wiki page that cited it and note the correction — don't
  touch the raw file. Every raw file is the permanent, re-derivable
  ground truth; if the wiki is ever wrong or lost, it should be
  reconstructable from `raw/` alone.
- **`wiki/`** — LLM-owned markdown. This is the compounding asset. Pages
  here get created, edited, merged, and re-linked over time as new
  sources come in. Nothing here is immutable.
- **`AGENTS.md`** (this file) — the schema and workflow contract.

## Page types

All wiki pages are markdown with YAML frontmatter. Frontmatter is
machine-written and machine-read; keep it valid YAML and keep keys
consistent with the schemas below. Body content is where the actual
knowledge lives — frontmatter is metadata for linking/search/lint, not a
substitute for prose.

Use `[[wiki-link]]` style links (Obsidian-compatible) for any reference to
another wiki page, using the page's slug (filename without `.md`). Do not
use raw relative markdown links between wiki pages — `[[slug]]` only. Links
to `raw/` sources use normal markdown links with the repo-relative path.

### `wiki/entities/<slug>.md`

A specific, nameable thing: a person, project, company, tool, place, or
similar. One page per real-world entity — if two names refer to the same
thing, merge into one page and record the other name in `aliases`, don't
create a second page.

```yaml
---
title: <canonical display name>
type: entity
entity_type: person | project | company | tool | place | other
aliases: []
created: <ISO 8601 date, first seen>
updated: <ISO 8601 date, last edited>
sources: [<raw/ paths this page was built or updated from>]
status: active | stale | deprecated
---
```

Body: short summary paragraph first (this is what gets read during query
synthesis without opening the full page), then details, then a
`## Sources` section listing what raw material contributed what claim if
that's not obvious from prose. Contradictions between sources go in a
`## Open questions / contradictions` section — don't silently pick one
and discard the other.

### `wiki/concepts/<slug>.md`

An idea, pattern, technique, or recurring topic that isn't a single
nameable entity — e.g. "context window management" or "OCI free tier
limits." Same frontmatter shape as entities but `entity_type` is omitted
and `related: []` (list of related concept/entity slugs) is included
instead.

```yaml
---
title: <concept name>
type: concept
aliases: []
related: []
created: <ISO 8601 date>
updated: <ISO 8601 date>
sources: []
status: active | stale | deprecated
---
```

### `wiki/summaries/<slug>.md`

A synthesized rollup that doesn't belong to one entity or concept —
typically the output of `query.py --file` (a filed answer) or a
higher-level digest spanning many sources (e.g. "2026-Q1 conversations
about job search"). Same frontmatter as concepts, plus:

```yaml
  generated_by: ingest | query
  covers: <free-text description of scope, e.g. a date range or theme>
```

### `wiki/index.md`

The content catalog — not a directory listing, a curated map. One entry
per wiki page: link, one-line description, type. Grouped by type
(Entities / Concepts / Summaries). This is what `query.py` reads first
to find candidate pages before falling back to FTS5 search, so keep
descriptions specific enough to disambiguate (not just the title
restated).

`ingest.py` updates this incrementally — appends/edits the relevant
line(s) for pages it touched, never regenerates the whole file from
scratch.

### `wiki/log.md`

Append-only. One line per ingest/query/lint operation, newest at the
bottom. Never edited retroactively except to append. Format:

```
- 2026-07-11T18:04:22Z [ingest] raw/conversations/claude/foo.md -> created entities/bar.md; updated concepts/baz.md
- 2026-07-11T18:10:03Z [query] "what did I decide about X?" -> entities/bar.md, concepts/baz.md
- 2026-07-11T19:00:00Z [lint] wiki/reports/2026-07-11.md -> 3 orphans, 1 stale, 0 dupes
- 2026-07-11T19:05:00Z [error] raw/conversations/chatgpt/qux.md -> DeepSeek API timeout, skipped
```

This is the audit trail — if the wiki ever looks wrong, `log.md` is where
to reconstruct why.

### `wiki/reports/`

Output of `lint.py` runs, one file per run (`wiki/reports/<date>.md`).
Never deleted automatically; these are historical lint snapshots.

## Ingest workflow (what `ingest.py` does per document)

1. Read the raw document in full.
2. Read `wiki/index.md` to see what already exists.
3. Extract candidate entities/concepts mentioned in the document.
4. For each candidate, decide **new page vs. edit existing page**:
   - Match against `index.md` titles/aliases first (cheap, exact-ish).
   - If ambiguous, use FTS5 search against existing page bodies.
   - **Always state which heuristic fired** (exact alias match / fuzzy
     title match / FTS5 hit / no match -> new page) in the log line and
     in the run's console output — this decision is the one most likely
     to need human correction, so it must stay visible, not buried.
   - Default to **editing an existing page** over creating a near-duplicate
     when in doubt; `lint.py` will flag near-duplicates but merging after
     the fact is more expensive than avoiding the split up front.
5. Write page creates/updates. Preserve everything in an edited page that
   the new source doesn't contradict — this is an integration, not a
   replacement. Update `updated` in frontmatter, append to `sources`.
6. If a new source contradicts an existing page, do **not** silently
   overwrite — add/update the `## Open questions / contradictions`
   section and still update the rest of the page normally.
7. Update `wiki/index.md` for every page touched.
8. Append one line to `wiki/log.md`.
9. On any failure (API error, malformed source, etc.), log an `[error]`
   line to `log.md` with the reason and move on — never let one bad
   source abort a batch.

## Query workflow (what `query.py` does)

1. Read `wiki/index.md`, ask the quick model (or simple heuristic) which
   pages look relevant to the question.
2. If `index.md` alone doesn't give confident candidates, run SQLite
   FTS5 search over wiki page bodies to narrow further.
3. Read the full text of the candidate pages (not just index summaries).
4. Call the deep model to synthesize an answer, **citing specific wiki
   pages** (`[[slug]]`) for each claim.
5. Append a `[query]` line to `log.md`.
6. If `--file` is passed, write the answer as a new
   `wiki/summaries/<slug>.md` page and update `index.md`.

## Lint workflow (what `lint.py` does)

Read-only with respect to `wiki/*.md` content pages — it **never deletes
or auto-edits** a content page. It only:

- Detects orphan pages (no other page links to them via `[[slug]]`).
- Detects stale pages (old `updated` timestamp, or superseded by a newer
  page that contradicts it).
- Detects coverage gaps (a slug mentioned via `[[slug]]` that doesn't
  exist yet, or a concept named repeatedly across pages with no
  dedicated page).
- Detects duplicate/near-duplicate pages (high text-overlap or same
  aliases pointing at two slugs).
- Writes findings to `wiki/reports/<date>.md` as a proposal list, and
  appends a `[lint]` summary line to `log.md`.

Use the quick model for classification-style lint checks (duplicate
detection, staleness heuristics) to keep it cheap enough to run on a
timer.

## Slugs and filenames

- Slug = lowercase, hyphen-separated, derived from canonical title
  (e.g. `title: "Oracle Cloud Always Free Tier"` -> `oracle-cloud-always-free-tier.md`).
- Slugs are stable once created — renaming a page means updating every
  `[[old-slug]]` reference across the wiki, so avoid it unless necessary.

## Non-negotiables

- Never edit or delete anything under `raw/`.
- Never silently discard a contradiction — surface it.
- Never let a single bad source, API error, or malformed page crash a
  batch run — log and continue.
- Every wiki page touched in a run gets its `index.md` entry updated in
  the same run. `index.md` drift is a bug.
