# Topic classification experiments and production rollout

Experiments conducted September 28, 2026. This document consolidates the complete model grids, follow-up evaluations, label audit, adaptive-topic rules, and final production decision. Temporary benchmark code, prediction exports, JSON reports, and experiment logs were removed after consolidation. Only the production classifier, its training command, stored artifact, metadata, and tests remain.

## Data and evaluation

Stories use 512-dimensional text-embedding-3-small embeddings of normalized titles and HN post bodies (input version 2). Linked article contents are not embedded. Training samples use story IDs divisible by three from the preceding year; validation/calibration uses older data than test. Repeated canonical article URLs and case-folded subject inputs are removed chronologically. Dead/deleted stories, inactive topic labels, missing version-2 vectors, and reserved editorial/fixture examples are excluded.

Initial experiments: 102,836 training, 12,016 validation, 9,243 test stories; dates 2025-09-28 through 2026-09-28, with the final 14 days reserved for test and the preceding 14 for validation. The live database changed during experimentation: later conventional metrics use 8,978 test stories; the explicit September 1–14 evaluation uses 11,861. Adaptive-set tests freeze those 11,861 IDs and labels but read current inputs and older training rows. These are not identical retrains of a frozen historical database.

Existing assignments are weak labels. The prototype router reproduced every initial test decision, and no cached semantic reviews were present at the initial audit. The 18 reserved editorial examples were selected errors, not a representative test set. All 15 old routing-fixture entries reference obsolete topic IDs. Previously explored hyperparameters overlap September 1–14, so follow-up results on that interval are exploratory. The test set was inspected repeatedly across experiments.

Conventional accuracy scores one label per story, including queue/reject as a 207th class. Macro F1/recall weight classes equally; these are not accuracy against independently adjudicated semantic labels. Selective agreement excludes low-confidence predictions using thresholds selected on validation only. Additional topic correctness cannot be inferred from single-label data.

## Initial lightweight models

| Configuration | Validation agreement | Test agreement | Test macro F1 | Editorial correct /18 | Selective test coverage | Selective agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| prototype | 100.00% | 100.00% | 1.0000 | 0 | 76.37% | 100.00% |
| embedding_ridge_a1 | 43.10% | 44.40% | 0.2329 | 0 | 2.69% | 94.38% |
| embedding_ridge_a10 | 41.75% | 43.03% | 0.2064 | 0 | 2.33% | 93.49% |
| embedding_sgd | 68.12% | 68.33% | 0.7306 | 0 | 28.55% | 95.11% |
| tfidf_sgd | 47.50% | 47.24% | 0.4515 | 2 | — | — |

Embedding SVM trials were stopped because fitting was taking too long; no SVM result was reported. Ridge used alpha 1/10. SGD used modified Huber loss, alpha 1e-5, random seed 42. Text features used up to 60,000 word unigrams/bigrams, min_df=2, sublinear TF-IDF.

## Exact cosine kNN: full grid

| Configuration | Validation agreement | Test agreement | Test macro F1 | Editorial correct /18 | Selective test coverage | Selective agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| knn_k1_uniform | 47.64% | 47.51% | 0.4771 | 1 | — | — |
| knn_k1_distance | 47.64% | 47.51% | 0.4771 | 1 | — | — |
| knn_k3_uniform | 52.66% | 53.20% | 0.5195 | 0 | — | — |
| knn_k3_distance | 52.04% | 51.49% | 0.5180 | 0 | — | — |
| knn_k5_uniform | 56.35% | 56.35% | 0.5715 | 0 | — | — |
| knn_k5_distance | 56.57% | 56.14% | 0.5726 | 1 | — | — |
| knn_k11_uniform | 60.22% | 60.08% | 0.6142 | 1 | 14.38% | 95.94% |
| knn_k11_distance | 60.79% | 60.61% | 0.6263 | 1 | 18.01% | 94.29% |
| knn_k21_uniform | 62.18% | 62.82% | 0.6322 | 1 | 21.26% | 95.01% |
| knn_k21_distance | 62.83% | 63.20% | 0.6461 | 1 | 20.98% | 95.05% |
| knn_k51_uniform | 62.98% | 63.46% | 0.6340 | 1 | 22.50% | 95.34% |
| knn_k51_distance | 63.72% | 63.70% | 0.6427 | 1 | 23.82% | 94.87% |

k = 1/3/5/11/21/51, uniform/inverse-distance voting; exact batched cosine search. The retained vectors and labels occupied 201.64 MiB, and test neighbor search took 16.15 seconds.

## Naive Bayes: full grid

| Configuration | Validation agreement | Test agreement | Test macro F1 | Editorial correct /18 | Selective test coverage | Selective agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gaussian_nb_1e-09 | 69.06% | 69.34% | 0.7775 | 0 | 38.65% | 95.24% |
| gaussian_nb_0.001 | 69.03% | 69.36% | 0.7779 | 0 | 38.57% | 95.34% |
| gaussian_nb_0.1 | 66.35% | 66.83% | 0.7617 | 1 | 36.82% | 95.15% |
| multinomial_nb_0.1 | 38.68% | 38.99% | 0.3347 | 2 | — | — |
| complement_nb_0.1 | 42.88% | 42.08% | 0.4006 | 2 | — | — |
| bernoulli_nb_0.1 | 22.51% | 24.18% | 0.1170 | 0 | — | — |
| multinomial_nb_1 | 34.30% | 34.83% | 0.1671 | 0 | — | — |
| complement_nb_1 | 45.01% | 43.76% | 0.4176 | 1 | — | — |
| bernoulli_nb_1 | 23.70% | 24.09% | 0.0052 | 0 | — | — |
| multinomial_nb_10 | 23.86% | 24.05% | 0.0053 | 0 | — | — |
| complement_nb_10 | 41.76% | 41.51% | 0.3355 | 0 | — | — |
| bernoulli_nb_10 | 23.40% | 23.59% | 0.0019 | 0 | — | — |

Validation-selected family settings: gaussian_nb_1e-09, multinomial_nb_0.1, complement_nb_1, bernoulli_nb_1.

Gaussian NB used raw embeddings and variance smoothing 1e-9/1e-3/0.1. Multinomial used word counts, Complement used TF-IDF, Bernoulli used binary presence, each alpha 0.1/1/10. Selective confidence uses log-posterior gaps; raw posteriors saturated.

## Gaussian families: full grid

| Configuration | Validation agreement | Test agreement | Test macro F1 | Editorial correct /18 | Selective test coverage | Selective agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| lda_auto | 65.48% | 65.17% | 0.7022 | 0 | 19.68% | 94.17% |
| lda_0.01 | 64.37% | 64.02% | 0.6850 | 0 | 16.61% | 94.66% |
| lda_0.1 | 64.96% | 64.76% | 0.6967 | 0 | 18.71% | 94.33% |
| lda_0.5 | 66.44% | 66.50% | 0.7257 | 0 | 26.51% | 94.49% |
| qda_pca32_0.01 | 55.40% | 57.59% | 0.5516 | 1 | 10.78% | 96.18% |
| qda_pca32_0.1 | 56.22% | 58.17% | 0.5646 | 1 | 13.19% | 95.49% |
| qda_pca32_0.5 | 57.64% | 58.77% | 0.5833 | 3 | 14.57% | 94.95% |
| qda_pca64_0.01 | 58.49% | 59.31% | 0.5618 | 1 | 14.25% | 95.06% |
| qda_pca64_0.1 | 60.86% | 61.05% | 0.6174 | 1 | 17.31% | 94.94% |
| qda_pca64_0.5 | 63.70% | 63.50% | 0.6734 | 1 | 23.26% | 95.35% |
| qda_pca128_0.01 | 51.64% | 52.04% | 0.3605 | 1 | 6.21% | 94.43% |
| qda_pca128_0.1 | 56.95% | 56.49% | 0.5073 | 1 | 12.29% | 94.10% |
| qda_pca128_0.5 | 63.02% | 62.59% | 0.6784 | 2 | 22.11% | 94.52% |
| gmm_diag_1 | 69.03% | 69.35% | 0.7776 | 0 | 38.62% | 95.27% |
| gmm_diag_2 | 69.82% | 70.20% | 0.7626 | 1 | 33.24% | 96.13% |
| gmm_diag_4 | 69.37% | 69.17% | 0.7350 | 1 | 27.56% | 95.37% |
| gmm_diag_8 | 66.80% | 67.79% | 0.7150 | 1 | 21.28% | 94.76% |

Validation-selected family settings: lda_0.5, qda_pca64_0.5, gmm_diag_2.

LDA used 512 dimensions with shrinkage auto/0.01/0.1/0.5. QDA used training-only whitened PCA at 32/64/128 dimensions with shrinkage 0.01/0.1/0.5 and the scikit-learn 1.9.1 eigen solver. Mixtures used diagonal covariance, 1/2/4/8 components, empirical class priors, covariance floor 1e-6, one initialization, seed 42, max_iter=200. Rare-topic component counts were capped at training examples //10. All mixture fits converged.

## Unfiltered conventional metrics: refreshed database

Test stories: 8,978. Each model emits one label; no confidence filtering.

| Model | Accuracy | Macro precision | Macro recall | Macro F1 |
| --- | ---: | ---: | ---: | ---: |
| gmm_diag_2 | 70.18% | 69.71% | 86.99% | 76.25% |
| gaussian_nb_1e-09 | 69.36% | 70.80% | 89.37% | 77.74% |
| embedding_sgd | 68.53% | 74.43% | 75.92% | 73.14% |
| lda_0.5 | 66.50% | 65.87% | 83.52% | 72.57% |
| knn_k51_distance | 63.68% | 80.33% | 58.53% | 64.23% |
| qda_pca64_0.5 | 63.44% | 61.49% | 76.33% | 67.24% |
| tfidf_sgd | 47.01% | 52.19% | 42.11% | 44.89% |
| embedding_ridge_a1 | 44.25% | 52.88% | 18.97% | 23.18% |
| complement_nb_1 | 43.57% | 44.00% | 43.37% | 41.75% |
| multinomial_nb_0.1 | 38.68% | 42.48% | 31.68% | 33.33% |
| bernoulli_nb_1 | 24.06% | 1.59% | 0.67% | 0.47% |

## September 1–14 inclusive, UTC

Test stories: 11,861. Each model emits one label; no confidence filtering.

| Model | Accuracy | Macro precision | Macro recall | Macro F1 |
| --- | ---: | ---: | ---: | ---: |
| gmm_diag_2 | 70.03% | 70.42% | 86.46% | 76.36% |
| gaussian_nb_1e-09 | 69.26% | 71.09% | 89.20% | 77.86% |
| embedding_sgd | 68.96% | 77.27% | 72.72% | 73.32% |
| lda_0.5 | 66.78% | 66.61% | 84.38% | 73.39% |
| qda_pca64_0.5 | 63.89% | 62.47% | 76.06% | 67.64% |
| knn_k51_distance | 63.68% | 80.19% | 58.75% | 64.73% |
| tfidf_sgd | 47.59% | 54.85% | 42.76% | 46.65% |
| complement_nb_1 | 45.00% | 47.35% | 45.21% | 44.20% |
| embedding_ridge_a1 | 43.23% | 52.20% | 18.76% | 23.09% |
| multinomial_nb_0.1 | 38.64% | 44.66% | 32.46% | 34.83% |
| bernoulli_nb_1 | 23.83% | 3.53% | 0.71% | 0.53% |

## Label and rejection audit

Of the initial 9,243 test stories, 7,059 had topic assignments, 1,587 were queued because competing prototype scores were too close, and 597 were below the fit floor. The prototype floors were cosine fit 0.2862 and inter-topic margin 0.02. Training the entire queue as a semantic class combined ambiguity with low topical fit. Every topic prediction for a queued story counted as wrong. This accounted for 65.4% of Gaussian NB mismatches.

| Variant | Existing assigned topic in top 1 | Top 2 | Top 3 | Top 5 |
| --- | ---: | ---: | ---: | ---: |
| gaussian_nb_queue_as_class | 86.51% | 94.69% | 97.44% | 99.01% |
| gmm2_queue_as_class | 87.58% | 96.44% | 98.26% | 99.15% |
| gaussian_nb_assigned_only | 86.51% | 94.69% | 97.44% | 99.01% |
| gmm2_assigned_only | 87.58% | 96.44% | 98.26% | 99.15% |

Removing queued training rows did not materially change generative-model ranking among actual topics. A naive multi-topic fit-floor rule produced 4.41 topics/story, up to 19. Restricting to topics above the fit floor and within 0.02 of the best produced 7,109 single-candidate stories, 1,537 multiple-candidate stories, and 597 with no candidate. These are candidate counts, not validated relevance.

## Adaptive probability sets

The tested rule returns the smallest ranked topic set reaching cumulative probability 50%, 70%, or 90%, capped at three. Queue is removed before normalizing candidate probabilities. Evaluation uses only the 9,072 stories with stored topics; the 2,789 queued stories have unknown memberships. Hit rate means at least one returned topic matches the stored label, not that all returned labels are correct.

Temperature scaling minimizes validation log-loss using a positive scalar T. It softens probabilities without changing ranking. Temperatures were 8.383 for Gaussian NB and 8.905 for GMM2 in this experiment. Calibration used August 18–31 assigned stories, with no test fitting. Raw Gaussian scores were highly overconfident; this adjustment is not a guarantee of calibrated coverage or independent multi-label membership probabilities.

| Model | Scores | Target | Stored label found | One | Two | Three | Mean labels | Capped below target |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gaussian_nb | raw | 50% | 86.35% | 99.91% | 0.09% | 0.00% | 1.001 | 0.00% |
| gaussian_nb | raw | 70% | 87.13% | 97.71% | 2.29% | 0.00% | 1.023 | 0.00% |
| gaussian_nb | raw | 90% | 88.07% | 94.78% | 4.98% | 0.24% | 1.055 | 0.00% |
| gaussian_nb | temperature_scaled | 50% | 89.33% | 89.91% | 9.13% | 0.96% | 1.110 | 0.17% |
| gaussian_nb | temperature_scaled | 70% | 93.47% | 74.38% | 18.85% | 6.77% | 1.324 | 2.00% |
| gaussian_nb | temperature_scaled | 90% | 96.33% | 51.48% | 23.59% | 24.93% | 1.735 | 14.55% |
| gmm2 | raw | 50% | 86.96% | 99.93% | 0.07% | 0.00% | 1.001 | 0.00% |
| gmm2 | raw | 70% | 87.79% | 97.78% | 2.22% | 0.00% | 1.022 | 0.00% |
| gmm2 | raw | 90% | 89.02% | 95.03% | 4.81% | 0.17% | 1.051 | 0.00% |
| gmm2 | temperature_scaled | 50% | 89.69% | 91.05% | 8.02% | 0.93% | 1.099 | 0.14% |
| gmm2 | temperature_scaled | 70% | 93.75% | 76.25% | 17.92% | 5.83% | 1.296 | 1.70% |
| gmm2 | temperature_scaled | 90% | 96.58% | 54.33% | 23.34% | 22.33% | 1.680 | 12.56% |

At adjusted GMM 70%, 2,155 stories received extra labels: 617 recovered the stored label, 1,316 already had it first, and 222 still missed it. At 50% these counts were 812 / 249 / 379 / 184; at 90%, 4,143 / 874 / 3,064 / 205 (expanded / recovered / already first / still missed). “No improvement” is not evidence that secondary labels are incorrect: additional labels may be valid even if the primary already matched.

Gaussian NB counts (expanded / recovered / already first / still missed): 50% = 915 / 273 / 435 / 207; 70% = 2,324 / 649 / 1,410 / 265; 90% = 4,402 / 908 / 3,237 / 257.

Examples at adjusted GMM 70%: an open-source Python AI-agent framework received AI agents (53.5%) and Python (38.0%); “The Battle over Dyslexia” received Constitutional rights (34.5%), Education (30.0%), Mental health (18.2%). Questionable expansion included a concrete-from-human-waste story receiving Food/nutrition and Archaeology after Recycling. A UFO/aliens headline added Immigration, illustrating that ambiguity can produce irrelevant candidates. These examples were not independently re-labeled.

## Production decision and implementation

Chosen: two diagonal Gaussian components per active topic, 512-dimensional embeddings, empirically fitted class priors and validation-fitted temperature. The runtime uses numpy/scipy arrays from `topic_classifier/models/model.npz`; no pickle and no per-story model/API fitting. Artifact metadata is embedded in the NPZ, with a readable companion `topic_classifier/models/model.json`.

New, live, version-2 embedded stories without an existing primary assignment or queue entry receive the smallest ranked set reaching cumulative 0.70, with at most three topics. A capped set below 0.70 is still assigned as tested, with `target_reached=0` stored for inspection. Existing queued stories and historical assignments are not bulk reclassified. Editorial overrides receive exactly their specified topic.

`story_topics` retains the primary topic and historical cosine diagnostics for archive/monthly-maintenance compatibility. `story_topic_labels` records ranks, calibrated probabilities, model version, assignment time, and whether the cumulative target was reached. `story_topic_memberships` exposes primary plus secondary topics to topic pages, API browsing, timelines, and newsletter candidate selection. The global timeline retains one primary row/story. Delete/update/replacement triggers clear secondary labels when a primary assignment changes. Content/embedding invalidation deletes the primary and therefore its secondary labels.

The daily refresh now calls the stored classifier rather than subject-review API calls. The existing semantic-review path remains for explicit monthly maintenance; scheduled daily/weekly jobs already skip discovery. A taxonomy-ID mismatch stops filing and asks for retraining rather than silently ignoring new topics. The prototype model in `data/topic_model.npz` remains required for map layout and monthly maintenance; it was not an experimental GMM artifact and is retained.

## Fit, operate, and recover

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m topic_classifier.train
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m topic_classifier --publish dist
bash scripts/deploy_site.sh dist
```

Training reads a consistent database snapshot, uses preceding-year labeled primary assignments, reserves the newest 14 days for temperature calibration, deduplicates inputs, and excludes previous GMM outputs to avoid feeding its own predictions back into training. Every active topic must have enough examples for two components. All mixture fits must converge. The NPZ is validated and replaced atomically, and its embedded metadata is authoritative. Reload uses artifact modification time.

The one-off filing command holds the same worker lock as the daily refresh and commits assignment batches together. It does not claim that HN was fetched, trigger monthly discovery, or send newsletters. Deployment uses the existing isolated Cloudflare Pages upload. Rolling back an artifact requires matching active topic IDs; reverting new memberships can be scoped by `model_version`/`assigned_at` rather than changing the historical archive.

## Production fit metadata

- `model_id`: `"gmm2-ae7158b77d095be6"`
- `artifact_sha256`: `"465b8071528e35d5657c0973d71f6995b5d3634e3f5207e4dbd3fc20928fb41d"`
- `temperature`: `11.983330607953983`
- `counts`: `{"train": 84515, "calibration": 10198}`
- `topic_count`: `206`
- `calibration_top1_agreement`: `0.7823102569131202`
- `calibration_logloss_raw`: `6.74125907089465`
- `calibration_logloss_scaled`: `0.9408202757256146`
- `trained_at`: `"2026-09-28T21:03:59.274056+00:00"`
- `train_start`: 2025-09-28T17:31:24+00:00
- `calibration_start`: 2026-09-14T17:31:24+00:00
- `end_exclusive`: 2026-09-28T17:31:24+00:00

The final production refit uses newer historical data than the benchmark; its calibration temperature and validation agreement therefore differ from the experiment. Calibration is used for fitting the temperature and is not an independent final accuracy estimate.

## Verification and provenance

Checks cover direct Gaussian-density equivalence, cumulative thresholds, three-label cap, empty/invalid embeddings, production daily integration without reviewer API calls, editorial precedence, queued-story preservation, idempotence, secondary-topic browsing/newsletter inclusion, global-feed deduplication, content invalidation, and taxonomy mismatch failures. Full-suite and deployment results are appended below.

Source aggregate report SHA-256 values (files removed after consolidation):

- `topic-classifier-benchmark.json`: `5491a95c10626bcd8c4321dee74e755888f322db5ef761bc13eff01573145ff8`
- `topic-knn-benchmark.json`: `573a674a8831055bf2d4fa3e8afda271185d22609bba8ce5635c1074f77c2ec8`
- `topic-bayes-benchmark.json`: `363e551b33b95845e6a856c206548b0be21015b7e6b9783016860f39c6ee05c7`
- `topic-gaussian-benchmark.json`: `44a0c5cfde5688c664e5523548dd69cd648b525da7a50cbcde786758620e706b`
- `topic-classifier-audit.json`: `2f9bd0aca3bf5c86c2a11f285c858a9391719cb028d105b39b240dcbee93fec4`
- `topic-traditional-metrics.json`: `9faaefa29b0a237b7fd1c242b83e3b173dc81d9ec37c7df333803a9ef24d212a`
- `topic-september-01-14-metrics.json`: `611a9264910601366d257c1d62ad68b713e2749fac92cba26a94cc8314fd54f2`
- `topic-september-prediction-sets.json`: `76b5619297c2154ea4fe25fff4a6d151e27bcaa0efa4229e703960cab9b3917c`
- `gaussian-nb-multilabel-counts.json`: `f3aec3b6ce3ddbb8721e415fdb4addeb93d5d5ec85b1e8889d0d5aa47c0e2e49`

Background: [Gaussian discriminant classifiers](https://scikit-learn.org/stable/modules/lda_qda.html), [Gaussian mixtures](https://scikit-learn.org/stable/modules/mixture.html), [probability calibration](https://scikit-learn.org/stable/modules/calibration.html), [multi-label classification](https://scikit-learn.org/stable/modules/multiclass.html).

## Production rollout — September 28, 2026

- All 122 Python tests passed. The deployment gate also passed all 22 JavaScript tests and six desktop/mobile browser checks against the exact upload bundle.
- A read-only smoke check on 100 recent production embeddings returned 55 single-topic, 29 two-topic, and 16 three-topic sets. These are output-size checks, not accuracy measurements. Artifact SHA-256 matched the value above.
- Applied the additive membership schema to the production database and ran the stored classifier under the worker lock. There were zero eligible pending embedded stories: zero stories filed and zero memberships inserted. Existing historical assignments and queue entries were preserved.
- The production daily worker now imports the GMM classifier and will use the stored artifact for new stories. Its daily and weekly timers remain enabled. The Python worker and model run on this production host; Cloudflare serves the exported site.
- Cloudflare confirmed deployment at https://783d41c3.hackeratlas.pages.dev. Production https://hackeratlas.com/ returned HTTP 200 and referenced release `e2fb86dc98224a97bf471e4ac77a0202`; the release's topic-data endpoint also returned HTTP 200. The deployment-specific URL rejected the plain Python HTTP client with 403, so the live domain was checked using curl with a browser user agent.
- Removed the five temporary classification experiment scripts and 19 associated report/prediction artifacts after consolidating their results here. Retained the production classifier, refit command, fitted artifact, tests, and existing prototype model needed for map maintenance. Removed the temporary production export after successful upload.


## Full-dataset reclassification — September 29, 2026

At the user's request, loaded the existing fitted artifact (`gmm2-ae7158b77d095be6`) without refitting and replaced the display assignments for every live post with a current version-2, 512-dimensional embedding. This supersedes the initial rollout's decision to leave historical assignments and queues unchanged. Posts without usable current embeddings, and dead/deleted posts, were outside this pass.

The operation held the site-update and worker locks, saved the previous assignment/queue/editorial tables, and replaced labels in one SQLite transaction. It reused the production classifier and retained all 18 editorial overrides. The primary table, ranked labels, and display membership view were checked for agreement before commit. Successfully classified queued posts were removed from the queue.

| Result | Posts |
|---|---:|
| Classified | 2,129,667 |
| Previously queued, now assigned | 445,336 |
| Existing primary topic changed | 205,070 |
| Return 1 topic | 1,175,166 |
| Return 2 topics | 503,713 |
| Return 3 topics | 450,788 |
| Three-topic cap reached below 70% cumulative probability | 250,009 |
| Editorial overrides preserved | 18 |

These assignments yield 3,534,956 display memberships. The counts describe model outputs, not measured semantic accuracy. Inference and transactional validation took 400 seconds. The temporary one-pass script was removed after successful completion.

Recovery snapshot: `data/backups/topic-labels-before-gmm-1790633908.sqlite` (original `story_topics`, `story_topic_labels`, `classification_queue`, and `editorial_decisions` tables). Machine-readable run summary: `data/topic-reclassification-20260929.json`. The recovery copy preserves the pre-GMM historical labels; bulk relabeling means those old labels are no longer in the primary production table and should be recovered from this snapshot if needed for future independent evaluation/training.

Post-commit verification: all 109 sampled posts matched fresh model predictions (topics, probabilities, and threshold-reached flags). Nine sampled secondary memberships were verified in the generated topic archives. The complete export contains all 206 topics. Deployment checks passed 22 JavaScript tests and 20 desktop/mobile browser checks against the rebuilt bundle.

Cloudflare deployment completed at https://b9a5d38a.hackeratlas.pages.dev. Verified https://hackeratlas.com/ and the astronomy topic page serve release `2366e66fcf174cd586e0d7cb438dc824`. Three newly assigned secondary memberships were also verified in the live 2020 astronomy archive. The temporary export was removed after verification; the recovery snapshot and run summary are retained.
