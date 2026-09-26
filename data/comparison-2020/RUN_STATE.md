# Running experiment state (not final results)

User authorized completing: embed all 2020–today, then compare static all-history clustering against initial 2020–2023 clustering plus weekly 2024–today ingestion; report production readiness from actual results.

Embedding process: unified exec session 20039, log data/comparison-2020-embed.log. Original session 45908 ended on transient DNS failure around 497k saved rows. Resumed successfully with connection/timeout retries. Currently approaching 1 million / 2,126,688 usable stories. 3 empty normalized inputs preserved in corpus.excluded_stories; original source unchanged.

All cluster/evaluation steps must wait for embedding-progress.json complete=true. Then run these two independent processes (OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1; network escalation authorized):

- .venv/bin/python data/comparison-2020/code/scripts/compare_history.py static > data/comparison-2020-static.log 2>&1
- .venv/bin/python data/comparison-2020/code/scripts/compare_history.py stream > data/comparison-2020-stream.log 2>&1

Use frozen code snapshot for both. Shared API ledger is cross-process transaction-safe, with $15 ceiling; public data only. No sending mail, deployment, subscriptions, or live database/model mutations. Both builds use same refit after chronological K selection; model selection and initialization sample 20k unique inputs; final fit processes every distinct input in bounded batches, and every eligible story is embedded/classified. All static source available, streaming DB initially strictly pre-2024.

After both processes finish:
- .venv/bin/python data/comparison-2020/code/scripts/measure_comparison.py
- .venv/bin/python data/comparison-2020/code/scripts/judge_comparison.py (bounded semantic mapping, coherence and duplicate diagnostics)

Then inspect actual topics, mismatches, targeted 15 known routing cases, event timeline, missed/emerging interests, assignment coverage (all and high-interest), raw partition agreement, and API ledger. Produce report/production-readiness-2020-2026/report.md with concrete results and honest readiness verdict. Static is a comparator, not ground truth; automated semantic grading alone cannot certify accuracy. Method/gates documented in method.md. Do not imply a completed comparison before both runs complete.

Transport fixes and invalid-input handling were added/tested in production code. GPT-6 Luna low reasoning matched the existing 13/15 fixture result and took 19.4s for a complete 100-story response (vs 58.5s medium); default weekly classifier now uses low. Separate preflight usage is under report/production-readiness-2020-2026/preflight-usage.db (~$0.004). 64 tests passed before latest empty-subject regression; updated pipeline tests (19) passed afterward. Full final suite still should run after any further changes.

Review output remains capped at 100 uncertain recent stories in ONE request per week. New topics max3 naming/distinction calls; structural changes one batch up to3 merges and3 splits. Existing source code is copied with SHA256 manifest to data/comparison-2020/code; it includes all current preflight fixes. No full live rebuild or full replay from earlier requests ran before this experiment.

## Current execution update
Embeddings COMPLETE: 2,126,688; full finite/nonzero/dimension validation passed (embedding-validation.json). Full suite 65 tests passed; map observation focused tests also passed.
Build supervisor session 14788 running both static and stream workers; logs data/comparison-2020-{static,stream}.log, supervisor data/comparison-2020-builds.log. No model complete at update.
Post-analysis supervisor session 14642 waits for static initial map and complete streaming checkpoint, then runs frozen measure_comparison.py, frozen judge_comparison.py, and scripts/render_map_evolution.py automatically. Log data/comparison-2020-analysis.log.
Frozen compare_history.py now captures initial and every weekly map (maps/*.json and *.npz), hash updated BEFORE builds launched. Observation only; decision semantics unchanged. User emphasized topic-map evolution and quality as PRIMARY verdict; coverage ancillary.
Still must complete all replay, inspect semantic and quantitative outputs, manually evaluate examples, calculate evolution metrics/discovery timing where defensible, and write report.md with actual verdict. Do not stop after saying launched.

## Latest steering and execution
User strongly wants ONLY the two final topic lists and their comparison; extra viewer/automated judging scope dropped. Post-analysis supervisor PID55624/session14642 was terminated before it ran anything. Do not restart optional analysis machinery. Compare lists and evidence directly and report readiness concisely.
Static still running under original build supervisor session14788. Streaming initial model complete:124 topics. Week2 added Large Language Models; week3 added Custom GPT Ecosystem. Last seen checkpoint week13 (2024-03-31),126 topics.
Performance index embeddings_routing(input_version,id) added to stream DB after week11; same ordered7318 IDs/vectors verified (9.74s old vs3.78s indexed). Concurrent DDL caused old worker to fail with SQLite snapshot lock during classification. Resumed directly from checkpoint using frozen replay(), session91002, PID63237; progressing. Frozen decision source unchanged. Production weekly.py now ensures index before opening cursors;8 weekly tests pass. Logs retain old traceback plus resumption marker; inspect newest checkpoint.
User asked runtime twice; explained embeddings complete,143 sequential weekly model calls median38s =>~90min streaming alone,~70–85min remaining at week11. Acknowledge avoidable copying overhead and overbuilt evaluation. Do NOT claim done or stop until two lists and comparison ready unless user cancels. No subagents.

## Authoritative latest scope/run
User explicitly confirmed discovery-only replay, skipping per-story LLM reviews; 90min acceptable but wants preliminary quality estimate. Full replay stopped after14weeks; source static COMPLETE120topics, pre2024 initial124topics.
Discovery-only runner scripts/replay_discovery.py ACTIVE session68782, log data/comparison-2020-discovery.log; checkpoint discovery-progress.json; events discovery-weeks.jsonl. It reset stream.db using saved initial centers/names, deleted post2023 records, cleared semantic overrides, reclassified original1,252,901stories. ALL124original topic counts verified exactly before commit; marker discovery-reset.json. Initial fits/embeddings reused; no new naming calls for reset.
Calls frozen production.weekly() directly, not weekly.maintain(); embedding routing remains needed for topic membership, per-story LLM reviews absent. Added covering story_topics(topic,sim) index before replay (safe stoppedworker) for aggregate performance. Week1=6.7seconds. Will export stream-discovery-topics.json at completion. Static list static-initial-topics.json already available;120subjects.
Final task: finish discovery-only replay, compare final two lists + grounded examples for missed subjects/duplicates/boundaries; concise readiness judgement FOR DISCOVERYONLY. No need giant optional UI/metric/automatedjudge harness. User is frustrated by overcomplication and delays.
Preliminary concerns: static list no explicit AIcodingtools; Lisp/Clojure evidence includes ClaudeCode andvibecoding. Static LargeLanguageModels sample mixes learning/education genericheadlines; naming logs rejected2other LargeLanguageModels candidates. Potential remove-centroid-on-duplicate-name issue; don't claim causal proof yet. Old fullrun addedLLMweek2 andCustomGPTweek3; verifynewdiscovery-onlyrun independently.
