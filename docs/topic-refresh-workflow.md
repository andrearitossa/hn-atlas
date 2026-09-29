# Keeping the topic map useful a year from now

Proposal, 29 September 2026. The production workflow has **not** been changed.
The diagnostic is implemented in `scripts/backtest_topic_refresh.py`; its recorded
results are in `docs/topic-refresh-backtest.json`.

## Recommendation

Keep a stable directory of reader interests and continuously refresh the evidence
behind it. Discover subjects from the whole incoming stream, promote a few proven
changes, and keep temporary news events inside existing topics. Do not rebuild or
rename the directory every month.

A simple operational shape:

```
Daily:   fetch -> reuse/embed -> classify with active version -> publish
Weekly:  sample recent stories -> propose changes -> accumulate evidence
Monthly: review ready proposals -> validate next version -> promote or retain current
```

Use the existing SQLite database and daily systemd entry point. Weekly/monthly
steps are due checks with separate checkpoints, run after the daily publication.
A maintenance failure leaves the active version and normal daily updates usable.
Keep heavy preparation outside the writer lock; verify the starting version and
activate under the existing lock. One maintenance implementation, no streaming
platform, vector database, or per-story LLM calls.

## Why the current system will not maintain itself

These are code/data findings, not speculative accuracy estimates:

1. `scripts/update_site.sh` passes `--skip-discovery`. README claims that the daily
   job runs monthly maintenance, but the executable entry point does not.
2. The GMM always assigns one to three existing topics. Discovery in `refresh.py`
   only clusters `classification_queue`, so successful filing hides new subjects.
   In the snapshot, all **357,489** live version-2 embedded stories from the past
   year are assigned; **zero** are queued. Two queue rows exist outside that slice.
3. A new topic changes registry IDs. `topic_classifier.classify()` then rejects
   the old artifact. Discovery does not stage and retrain a matching GMM before
   adding a topic, so enabling the old monthly path can break the next refresh.
   An added center extending an existing topic also does not update GMM inference.
4. Training excludes any story with `story_topic_labels` to avoid self-training.
   Bulk reclassification filled that table: the same yearly slice has **zero**
   remaining eligible training stories. The pre-GMM backup retains weak historical
   labels; it must be an explicit source, not silently replaced by predictions.
5. Maintenance repairs primary memberships and clears secondaries through existing
   triggers; it does not validate all secondary labels used by newsletters/pages.
   Good primary-topic statistics would not prove good subscriber results.

Do not solve these problems by simply removing `--skip-discovery`.

## The proposed workflow

### Daily: serve fresh stories under a stable contract

Continue the inexpensive stored classifier. Record assignment version, confidence,
absolute fit, and input hash. A confident choice among existing topics is not
proof that the directory covers a story. Missing embeddings or filing backlogs
are operational failures, not evidence for creating a new topic.

Keep topic IDs and published URLs stable. A quiet topic remains available with its
archive and subscriptions; recent activity controls its prominence. Changes to a
name or scope are explicit versioned edits. Do not automatically delete, merge,
or split subscriptions when a cluster moves.

### Weekly: look beyond what the classifier already knows

Read a consistent snapshot of the last 28 days of **all** stories, including those
already assigned confidently. Deduplicate article URLs and normalized subjects.
Use existing embeddings and a bounded, uniform sample of at most 12,000 stories.
Clustering that whole sample is cheap enough; no rejection queue is required.
Separately sample weak-fit and ambiguous assignments for maintenance review.

Generate candidate groups with MiniBatchKMeans. Cluster count is an internal
resolution setting, not a target number of public topics. Match groups to stored
candidate exemplars across runs. Rank by repeated support, growth in share of
all HN stories, and gaps against topic descriptions. Similarity to existing
centers prioritizes review; it must not veto a new interest inside a broad topic.

Maintain three candidate states: **watch -> ready -> accepted/rejected**. Starting
heuristics: at least 20 distinct stories, five sources, and evidence in three
weeks. Confirm with a later, non-overlapping fortnight using the candidate's
frozen definition. These are provisional noise filters, not validated relevance
thresholds. A young subject can enter watch immediately; lack of evidence in the
previous month must not prevent detecting something genuinely new. Rank and
retain the backlog rather than discarding it at each run.

A burst such as an AWS outage should remain news within AWS/reliability. A proposed
new subscription must explain a recurring reader interest not already delivered
by another topic. Existing company/ecosystem topics make a blanket ban on company
or product names inconsistent; decide by continuing usefulness and overlap.

### Monthly: make a small, evidenced change

Review at most ten ready proposals, plus ten existing topics selected by rotating
coverage and drift. Include central examples, boundary examples, later confirmation
examples, and nearest competing topics. Use **GPT-6 Luna** only for these bounded
editorial packets. Require structured results and specific evidence IDs:

- already covered / transient event: reject or keep as a story collection;
- existing interest, new vocabulary: refresh reviewed exemplars;
- distinct continuing interest: propose a new topic and explicit scope;
- unclear: keep watching, without broadening the name to hide a mixed cluster.

Keep evidence used to propose/name a topic separate from evidence used to test its
membership. A second call to the same LLM is a useful consistency check, not an
independent human ground truth. Initially review proposed public changes manually;
automate routine promotions only after prospective quality has been measured.
This is a rollout recommendation, not a permission requirement for this analysis.

Start with at most two public structural changes per month to limit disruption.
This is a rate limit, not a belief that exactly 24 subjects emerge each year.
Carry ready proposals forward and expose their waiting time; revisit the cap if
valid subjects are being delayed. Refreshing evidence within existing interests
is likely to be the more common change.

### Build and activate a complete version

Add an explicit reviewed-label store keyed by story/input hash, with label source,
review date, taxonomy version, and supersession history. Import the pre-GMM backup
as **weak historical evidence**, retaining that provenance. Keep human corrections
and accepted reviews distinct from generated memberships. Preserve old exemplars
for quiet topics instead of dropping them because they fall outside a rolling year.

Build the candidate taxonomy, classifier, and recent memberships together. New
classes need enough independently checked, varied examples and held-out positives
and negatives; do not treat the current two-row technical fitting minimum as
statistical support. Keep a candidate in watch if evidence is insufficient.
Retrain when reviewed evidence or scopes change, not just because a timer fired.

Validate exact active IDs, taxonomy hash (including descriptions), input versions,
editorial overrides, primary **and** secondary membership quality, and unexpected
assignment churn on a fixed recent sample. The live runtime currently checks IDs,
so description-hash enforcement needs implementation too.

Store immutable model artifacts and a release manifest. Prepare inactive
versioned memberships first; one database transaction switches the active version
pointer after all files are present and validated. Readers resolve taxonomy,
classifier, and memberships through that same pointer. Retain the previous version
for rollback and regenerate the public snapshot after activation. Never publish a
new registry with an old classifier. This version mechanism is proposed work,
not a claim about today's schema.

## Cost and operational limits

- Reuse existing 512-dimensional embeddings; no archive re-embedding.
- No new LLM call for ordinary story filing.
- Bound clustering input, reviewed packets, prompt length, output tokens, retries,
  and total run time. Cache reviews by evidence and taxonomy hash.
- Start with a **$5/month maintenance spending ceiling**, excluding the existing
  daily embedding bill. This is a chosen budget, not a quoted model price or
  measured monthly forecast. Reserve worst-case request cost against configured
  current rates before each Luna call; stop paid work when the budget is exhausted.
- Keep five operational measures visible: last successful maintenance, ready
  candidate age, reviewed primary/secondary precision, recent assignment churn,
  and spend. Use an unbiased rotating sample as well as targeted problem samples;
  targeted error rates cannot be reported as population accuracy.
- Failed maintenance must not block daily publication. Missed runs do one current
  assessment rather than replaying every missed month.

This takes the bounded batch updates from
[BERTopic's online modeling guidance](https://maartengr.github.io/BERTopic/getting_started/online/online.html)
and the evaluate-before-promotion/rollback discipline of
[Google SRE canary releases](https://sre.google/workbook/canarying-releases/).
It does not require adopting BERTopic's full stack or Google's infrastructure.
The comparison below tests one lightweight component, not either source's system.

## Bounded backtest and what it actually establishes

**77.06 seconds, zero API calls, zero production writes.** One read transaction,
12 monthly cutoffs from October 2025 through September 2026. At each cutoff:
6,000 sampled training stories from the prior 28 days and 3,000 unseen stories
from the next 14 days; 36,000 test stories in total. Baseline: 80 normalized cluster
centers fitted on August 4–31, 2025 and frozen. Challenger: 80 newly fitted centers
per cutoff. Two seeds, equal center counts, no present-day topic labels or curated
centroids. Canonical URL/subject duplicates from either training window are
excluded from the future test. Parameters were not tuned after seeing results.

| Measure | Result |
| --- | ---: |
| Mean frozen cosine loss | 0.465923 |
| Mean rolling cosine loss | 0.463394 |
| Relative geometric loss reduction | 0.543% |
| Rolling wins, seed × month | 23 / 24 |
| Candidate evaluations with ≥5 later matching posts | 215 / 240 |
| Mean matched-center cosine across seeds | 0.809 |

Two seed results on one month are not independent experiments. Neither a
significance test nor an accuracy confidence interval is claimed. The 215/240
count includes overlapping candidate subjects across seeds and months, not 215
unique discovered topics. Persistence uses a training-fitted similarity floor and
is intentionally a cheap screen; it does not validate the cluster's meaning.

Manual inspection of the top novelty-ranked candidate from every seed-42 month
found concrete counterexamples to using these metrics as promotion criteria:

- November's AWS-outage group had eight future matching posts, but its boundary
  examples included Amazon labor organizing and a cancelled game. It passed the
  persistence screen without proving a distinct subscription.
- June's data-center/power group had 36 future matches, but included heat-pump
  shopping at its boundary. Its subject is also already represented in today's
  directory, illustrating that novelty against a small frozen clustering baseline
  is not novelty against the real taxonomy.
- January's Paramount/Warner takeover group and February's Iran blackout each had
  only two later matches: fresh news can score as highly novel and then fade.

The result supports **cheap candidate discovery plus conservative review**. The
small geometric improvement gives no evidence for replacing the public taxonomy
with monthly reclustering. It does not prove that the proposed full maintenance
workflow will achieve year-ahead semantic relevance: editorial review, retraining,
and atomic promotion were not run in this diagnostic.

Limits: stored embeddings, present story bodies, and deletion flags are a
retrospective snapshot, not exact historical inputs. Current queue/label counts
are separate operational diagnostics, not reconstructed historical queue states.
The 80-cell test baseline is not the production 206-topic GMM; no production model
superiority is claimed. Rare subjects can be lost in sampling. There is no
independent semantic gold set, emerging-topic recall score, or discovery-delay
estimate here. The test checks rolling discovery feasibility and challenges
promotion assumptions; it is not an end-to-end annual acceptance test.

Reproduce:

```bash
timeout --kill-after=5s 1080s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  .venv/bin/python -u scripts/backtest_topic_refresh.py
```

The 1,080-second deadline plus five-second kill grace stays below 20 minutes.
The full local packets are in `report/topic-refresh/results.json` (ignored);
the checked-in summary preserves config, input fingerprint, all fold metrics,
and the top candidate packet for each seed/month.

## Assumptions from previous attempts that should be retired

- **Agreement with old labels equals truth.** Prior experiments explicitly found
  100% prototype agreement with labels produced by that prototype. Repeated use of
  September's evaluation data also made later model comparisons exploratory.
- **70% cumulative probability means 70% correct memberships.** These are
  normalized choices among known labels, not independent multi-label truth or
  an unknown-subject detector. Additional newsletter memberships need evaluation.
- **An empty queue means complete coverage.** The current forced-choice classifier
  makes the queue empty regardless of missing subjects.
- **A coherent, persistent cluster deserves a topic.** Git history contains
  “Year-End 2024 Recaps” and “Paris Olympics” attracting later unrelated/date-shifted
  stories. Our AWS example shows this issue survives a future persistence check.
- **More splits keep the map relevant.** The prior online-v2 run was explicitly
  stopped for excessive subdivision after 80 weeks with 257 topics; its artifact
  says `complete: false`. It is evidence of a failure mode, not a completed annual
  comparison proving an alternative wins.
- **Recovering a deliberately hidden Apple/Rust/Robotics cluster proves emergence
  detection.** Earlier checks measured the best small sample's agreement with
  existing labels, not unknown-topic recall, detection delay, or final acceptance.
- **Two LLM passes are independent validation.** They can share the same semantic
  blind spots. Preserve a small independent human-reviewed holdout.
- **Silence means obsolete.** Seasonal and specialist subscriptions should keep
  their identity; activity should change prominence before it changes taxonomy.

Evidence: `docs/topic-classification-experiments.md`, `docs/monthly-map-audit.md`,
and parent commit `d1f2868` artifacts
`data/comparison-online-v2/stopped.json`,
`data/comparison-2020/stale-topic-evidence.json`, and
`data/comparison-2020/recap-birth-evidence.json`.

## Practical rollout

First preserve training evidence and implement coherent version activation; these
are prerequisites, not optional optimization. Then run discovery in shadow mode
for four weeks, review actual candidate packets and subscriber-facing memberships,
and collect an independent prospective holdout. Only then promote the first
supported changes. Define precision/churn acceptance thresholds from that pilot
before automation. No current experiment justifies claiming a universal numerical
threshold for semantic quality.

Success a year from now means that a new continuing interest can become visible,
an established interest can acquire new vocabulary, and existing subscriptions
remain understandable. Topic count, raw activity, and agreement with yesterday's
model are supporting diagnostics, not that goal.
