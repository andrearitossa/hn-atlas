# Monthly topic map audit — 28 September 2026

Read-only inspection of `data/atlas.db` and the current maintenance code. No API calls or database edits. The 60-day snapshot covered live, nondeleted `story` posts from **30 July 2026 17:31 UTC** through **28 September 2026 17:31 UTC**, split into equal 30-day windows. The database was being refreshed during this audit, so later point counts may move slightly.

| Window | Stories | Current v2 embedding | Assigned | Queued | Embedded but pending filing |
| --- | ---: | ---: | ---: | ---: | ---: |
| Earlier 30 days | 30,165 | 30,165 | 21,718 | 6,735 | 1,712 |
| Recent 30 days | 29,679 | 29,679 | 19,173 | 5,918 | 4,588 |

All 59,844 stories had the title plus HN-body embedding input version 2. The 6,300 embedded but unfiled stories are a processing backlog, **not evidence of missing topics**. The queued 12,653 consist of 9,205 `ambiguous` and 3,448 `low_fit` decisions; those are the monthly gap search's actual input. Recent queues include 372 stories with at least 50 HN points, and pending filing includes 337 more such stories. The topic registry and topics table each have 206 rows; `topic_centers`, `topic_changes`, and `routing_reviews` were empty at this snapshot.

Recent topic traffic is very uneven. AI governance rose from 911 to 1,187 assigned stories between the earlier and recent 30 days; LLMs held near 1,004 to 947; agents 671 to 632; Apple 315 to 429. At the small end, sensory biology had 10 recent stories, Korean Peninsula affairs 11, Facebook policy 14. These numbers are useful watch signals, but natural interest varies and this sample alone does not justify deleting or widening any topic. The more substantive signal is that 1,956 recently assigned posts had stored vector similarity below 0.35, including 121 with at least 50 points.

Concrete existing-topic errors appeared in that lower-confidence slice. Apple ecosystem contains **AnkiDroid: Google Play no longer allowing Open Collective donation link** (HN 49520022, 924 points; fit 0.392) and **The Google Play app review process now regularly takes longer than a week** (49724927, 374 points; fit 0.431). Their titles and URLs point to Android/Google Play, with no Apple subject evident. AI assistants and agents contains **Dynamic Abliteration: Non-Destructive Refusal Suppression via Engram Steering** (49831201, 108 points; fit 0.373), which appears to concern model steering rather than assistants. These are manual examples to review, not an automated error-rate estimate.

The current monthly `discover()` searches only `classification_queue` posts from the last 60 days. It clusters them, requires popularity and multiple sites, proves a candidate on the preceding month, and asks an LLM whether to extend a topic, add a durable topic, or skip. This is a reasonably cautious **new-topic gap test**. It is not a map-quality audit: it cannot see misfiled assigned posts, and `classify()` normally skips any ID already in `story_topics` or `classification_queue`. A new accepted topic or extension triggers broad refiling, but otherwise existing assignments stay put. The scheduled daily command was also passing `--skip-discovery`, preventing even the gap test from running.

For an existing-topic maintenance check, add a bounded monthly review of current assignments after the daily backlog is cleared: sample high-attention posts with low fit or small margins from each topic, compare their subject against the topic description and plausible neighbors, and report clear errors for correction. Review growth, decline, and queue pressure as signals to choose samples; do not infer that a topic should be added or removed from volume alone. Keep new-topic decisions separate and require the current same-level, older-month proof before adding to the map. Because this database has no cached semantic `routing_reviews` yet, the first audit should treat the old assignments as unreviewed rather than assuming they have already passed the new subject check.


## Changes and bounded experiments

The daily entry point now includes monthly maintenance. It first samples up to
12 stories per existing topic: four spread over the 60-day window, up to four
high-attention stories, and weak-fit stories to fill the sample. The audit flags
clear subject mismatches; a separate classification pass must assign a different
topic (or no existing topic) before a story moves. Explicit editorial decisions
remain authoritative. `topic_health` records activity, sample size, flags, repairs,
and reasons. Low-volume topics are retained, not equated with obsolete topics.

Discovery now uses the most recent 30 days and an independent preceding 30-day
window. It no longer requires three 50-point hits in each candidate cluster.
Population, source diversity, semantic evidence, existing-topic scope, and held-out
cohesion checks remain. Incomplete editorial responses fail the monthly job rather
than silently advancing its checkpoint. The audit, discovery, and checkpoint commit
or roll back together.

Real-data experiments used an external 1,080-second timeout with a five-second
termination grace period. No experiment wrote to the production database:

- Original candidate/proof funnel: **66.2 seconds**, 117 candidates and 55 passing
  the preliminary gap-evidence gate. This was before the background filing backlog
  completed and is not a direct before/after quality comparison.
- Revised read-only diagnostic: **29.2 seconds**, including deliberate removal of
  Apple, Rust, and Robotics from the candidate generator.
- Controlled before/after recovery on one consistent database snapshot:
  **92.7 seconds**. The best candidate sample's agreement with existing topic
  assignments was Apple **100% → 95%**, Rust **100% → 100%**, Robotics **60% → 100%**.
  These are 20-story candidate samples, not recall over all stories or classification
  accuracy. The old Apple cluster included Google Play stories wrongly filed under
  Apple, illustrating why assignment agreement is an imperfect reference. The new
  Robotics sample contains a coherent robotics subject that can reach editorial
  review; this does not prove a new topic would ultimately be accepted.
- Local regression suite: **113 tests in 1.1 seconds**, followed by a passing
  additional rollback test. Tests cover repairs, reviewer disagreement, editorial
  overrides, invalid responses, quiet-topic retention, current-window discovery,
  and retry behavior.

Artifacts: `report/topic-maintenance/baseline.json`, `offline.json`,
`controlled-recovery.json`, and `robotics-ablation.json`. The repeatable current
check is `scripts/check_topic_map.py`; see README.md for timeout commands.

The larger live OpenAI audit and simulated real-data repairs require separate
approval: automatic approval review rejected expanding the earlier 15-story
newsletter approval to the 206-topic sample. That question is pending. No live
semantic-repair result or population-wide accuracy improvement is claimed.
