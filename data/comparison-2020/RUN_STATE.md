# Running experiment state (not final results)

User authorized completing: embed all 2020–today, then compare static all-history clustering against initial 2020–2023 clustering plus weekly 2024–today ingestion; report production readiness from actual results.

Embedding process: unified exec session 20039, log data/comparison-2020-embed.log. Original session 45908 ended on transient DNS failure around 497k saved rows. Resumed successfully with connection/timeout retries. Currently approaching 1 million / 2,126,688 usable stories. 3 empty normalized inputs preserved in corpus.excluded_stories; original source unchanged.

All cluster/evaluation steps must wait for embedding-progress.json complete=true. Then run these two independent processes (OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1; network escalation authorized):

- .venv/bin/python data/comparison-2020/code/scripts/compare_history.py static > data/comparison-2020-static.log 2>&1
- .venv/bin/python data/comparison-2020/code/scripts/compare_history.py stream > data/comparison-2020-stream.log 2>&1

Use frozen code snapshot for both. Shared API ledger is cross-process transaction-safe, with $15 ceiling; public data only. No sending mail, deployment, subscriptions, or live database/model mutations. Both builds use same refit after chronological K selection; initial fit samples 20k unique inputs across all available time, every eligible story is embedded/classified. All static source available, streaming DB initially strictly pre-2024.

After both processes finish:
- .venv/bin/python data/comparison-2020/code/scripts/measure_comparison.py
- .venv/bin/python data/comparison-2020/code/scripts/judge_comparison.py (bounded semantic mapping, coherence and duplicate diagnostics)

Then inspect actual topics, mismatches, targeted 15 known routing cases, event timeline, missed/emerging interests, assignment coverage (all and high-interest), raw partition agreement, and API ledger. Produce report/production-readiness-2020-2026/report.md with concrete results and honest readiness verdict. Static is a comparator, not ground truth; automated semantic grading alone cannot certify accuracy. Method/gates documented in method.md. Do not imply a completed comparison before both runs complete.

Transport fixes and invalid-input handling were added/tested in production code. GPT-6 Luna low reasoning matched the existing 13/15 fixture result and took 19.4s for a complete 100-story response (vs 58.5s medium); default weekly classifier now uses low. Separate preflight usage is under report/production-readiness-2020-2026/preflight-usage.db (~$0.004). 64 tests passed before latest empty-subject regression; updated pipeline tests (19) passed afterward. Full final suite still should run after any further changes.

Review output remains capped at 100 uncertain recent stories in ONE request per week. New topics max3 naming/distinction calls; structural changes one batch up to3 merges and3 splits. Existing source code is copied with SHA256 manifest to data/comparison-2020/code; it includes all current preflight fixes. No full live rebuild or full replay from earlier requests ran before this experiment.
