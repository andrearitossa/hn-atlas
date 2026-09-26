# Updated full comparison (active)

User requested a fresh static 2020–today versus pre-2024 seed + weekly 2024–2026 comparison, and a user-facing usefulness verdict. Do not stop at launching the job.

- Worker: unified exec session 21345, launched 2026-09-25 13:24 UTC with approved network access; command `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python scripts/retest_online.py > data/comparison-online-v2.log 2>&1`.
- New isolated DBs under this directory; corpus.db is a read-only-used symlink to the previously validated corpus. Live DB and previous experiments untouched.
- Frozen current root Python code is in `code/`, with hash manifest. Both static and seed builds use updated batch naming, then actual weekly classification/maintenance for all 143 weeks.
- API budget ceiling $15 in this run's api-usage.db. No embedding calls needed.
- Current stage: static initial database copy/checkpoint. No new comparison verdict yet.
- Runner is restartable from `static-ready.json`, `stream-ready.json`, and `replay.json`.
- Setup bottleneck found: initial copy maintained random hash index per row and wrote a giant WAL. `scripts/compare_history.py:init_db` now bulk-loads with rollback journaling, builds hash index after copying, then restores WAL. Three comparison tests passed. The already-running process has the old helper in memory: after static-ready.json is present, interrupt/restart at the seed boundary to pick up the faster helper without rebuilding static. Avoid stopping before the static marker unless needed for an actual failure.
- Progress/log: progress.json, replay.json, weeks.jsonl, latest-map.json, ../comparison-online-v2.log.
- When complete run `scripts/summarize_online_comparison.py`, inspect both complete lists and typical/varied story samples, inspect transitions and missing interests, and write a candid report. Metrics are coverage/overlap, not semantic accuracy.
- Method: report/comparison-online-v2-method.md. Report artifacts will go to report/comparison-online-v2/.

A checkpoint supervisor is now running in unified exec session **92049**. It waits for static-ready.json, verifies original worker PID 73342, interrupts it only after the committed static checkpoint, and resumes the same runner with the faster copy helper. The resumed PID will be in worker.pid. Poll session 92049 after the restart; session 21345 is expected to exit 130 from this intentional handoff. No manual restart is needed while the supervisor is healthy.
