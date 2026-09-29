# Codebase cleanup — 28 September 2026

## Findings

The project had two incompatible maintenance systems: a legacy weekly topic
registry and the curated daily refresh. They used different model artifacts,
different database defaults, and incompatible `topic_changes` schemas. The old
status command consequently failed against the curated database.

The curated publication database intentionally omitted embeddings; 2,126,688
vectors were stored in `data/comparison-online-v2/static.db`. The daily refresh
did not consult them and would regenerate its entire 95-day window. The older
`data/hackernews.db` also contained 4,180,782 stories absent from the curated
corpus, including history back to 2006. Deleting it as a duplicate would lose data.

The only installed daily job prepared an isolated newsletter test. It did not
update or deploy the main site. Two historical replay jobs were also running;
they and their monitors were stopped with the user's authorization.

## Resulting design

One canonical local database, `data/atlas.db`, retains the full story archive,
the existing embedding vectors, and the curated 206-topic map with its 116
permanent aliases and 18 editorial decisions. The initial merged counts were
6,310,950 stories and 2,126,692 embeddings. Source-to-target ID checks found zero
missing historical stories and zero missing source vectors. Four legacy vectors
without replacements were retained with their original input version.

The 2006–2019 archive is preserved as data; it is not silently reclassified under
a model curated on 2020–2026 stories. Old incompatible topic assignments were
not imported. Cloudflare D1 remains the remote operational store for signups,
feedback, and delivery state; it is not another copy of the story corpus.

`refresh.py` is the only corpus update command. Topic/model operations are in
`topics.py`; the weekly newsletter has one prepare-and-send entry point. Paths and
environment loading are explicit in `config.py`. The source-database workaround
added at the beginning of this task was removed after consolidation: runtime
updates no longer depend on an external embedding archive.

Historical simulations, training/comparison scripts, duplicated source snapshots,
legacy weekly entry points, and stale experiment reports were removed. The
newsletter test reads the canonical corpus instead of ingesting into a copy.

## Correctness changes

- Save the initial catch-up checkpoint before descending chunks commit. Retrying
  the interrupted original run starts at item 49,841,840, not its newest partial row.
- Embed the normalized title plus available HN post text, including linked posts.
  Body text is bounded to 6,000 characters; total input is bounded to 8,000 UTF-8
  bytes. External article pages and comment threads are separate content sources.
- Compare input hashes before reusing embeddings; changed inputs lose their old
  assignment and receive a new subject review.
- Keep a pending marker for edited stories outside the recent window, so older
  featured posts do not get stranded after an edit.
- Retain existing embeddings and file recent stories into the curated topic map.
- Validate database/model topic IDs before external calls.
- Report the successful refresh checkpoint in health/status output. A newest-story
  timestamp can advance during a failed run and is not itself proof of success.
- Pin Wrangler locally and use one daily update/export/deploy script.
- Run daily deployment and weekly newsletter preparation through persistent
  systemd user timers. The weekly job requires a successful database refresh within 36 hours;
  monthly topic discovery runs inside the first successful daily refresh of each month.

## Verification approach

Run the retained Python and JavaScript tests, verify SQLite integrity and preserved
IDs, then run live ingestion and review real inputs/assignments/featured outputs.
`scripts/review_pipeline.py` produces reproducible samples from the latest completed
refresh without additional API calls. Manual observations belong in
`docs/pipeline-quality.md`; test success alone is not evidence of semantic quality.

## Consolidation verification

The canonical database passed `PRAGMA main.quick_check` (`ok`) after its WAL
was checkpointed. The old source databases and one-time consolidation script
were then removed. The remaining `data/` directory is approximately 9.8 GB,
down from about 78 GB including the obsolete replay and comparison artifacts.
The live refresh started only after that cleanup and validation completed.

## Newsletter simplification

One Sunday 18:00 Stockholm workflow checks freshness, selects stories, and prepares
and sends issues. Preview uses the same selection/rendering path. The separate
SMTP delivery system, deployment dependency, duplicate freshness check, and weekly
success-file bookkeeping were removed. Delivery records remain the source of truth
for retries and duplicate prevention. These changes are code only, not deployed.

The follow-up newsletter simplification stores one HTML issue per topic/edition
in D1. Signup and cancellation use one subscription table; a small delivery ledger
lets the Worker report exact accepted-send counts without duplicate sends. Monday
catch-up keeps Sunday's edition and waits for the shared database lock. Custom topic
names, notes.json summaries, and plain-text rendering were removed. The schema
migration and new runtime remain staged, not deployed.

The historical 116 aliases were subsequently removed at the owner’s request. The database now contains only the 206 current topics; old topic IDs no longer redirect. Newsletter selection now uses weekly engagement shortlists, fetched-page overviews, and LLM selection; see README.md.

Monthly discovery has been restored independently of aliases: the curated map remains the baseline, while supported new topics can be added after the monthly quality checks. Old topic redirects remain removed.

Monthly maintenance now audits current topic assignments as well as missing subjects.
The daily entry point no longer passes --skip-discovery. Candidate evidence uses
two adjacent 30-day windows, and the viral-post gate has been removed. See
README.md and docs/monthly-map-audit.md for the bounded validation procedure.
