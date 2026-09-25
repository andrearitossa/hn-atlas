# Manually curated static map

This map starts from the 287-subject static candidate and applies explicit editorial decisions in `curation.json`. It contains 206 reader subscriptions. It is not a new clustering run or an automatically evolving taxonomy.

The plan records every original topic's destination, retained evidence centers, intentionally retired centers, renames, merge reasons, and manually selected anchors for literature, document tools, finance, visualization, and systems programming, plus 17 explicit story corrections. Existing 116-topic public IDs redirect to manually selected relevant subscriptions. New public IDs start at 1000, so an old link cannot silently acquire an unrelated meaning.

Merging preserves multiple source centers per subscription. Routing takes the strongest matching retained center and computes ambiguity against a different subscription, not another center belonging to the same subscription. Fifteen unreliable centers are excluded; their stories are scored against the remaining interests. Representative average centers are used only for the visual map and related-topic geometry.

## Rebuild and publish

The source SQLite database and its embeddings are private local artifacts. The output database contains public stories, curated assignments and routing abstentions; it does not duplicate embeddings or newsletter records. Keep the source database available for future manual rebuilds.

```bash
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/build_curated_static.py
HN_TOPIC_MODEL=data/curated-2020-2026/topic_model.npz .venv/bin/python publish.py --db data/curated-2020-2026/static.db --output dist
.venv/bin/python scripts/prepare_pages.py
npx wrangler pages deploy pages-dist --project-name hackeratlas --branch main
```

The builder refuses to overwrite an existing build. For another curation revision, copy the plan into a new output directory, update its source artifact paths as needed, and pass `--out`. The saved model includes all retained prototype centers and their stable topic IDs.

This is a static publication snapshot, not a database to point the automatic weekly worker at. Changes should go through the curation plan and validation. Existing subscriber records are not rewritten or emailed by this workflow. The public site's aliases preserve old browsing links; the signup catalog uses current topic IDs.

## Scope of validation

`report/curated-static/` contains a same-story comparison against the 287-topic source and an eight-title diagnostic audit of every curated subscription. These are retrospective model-assisted checks, not a guarantee that every assigned story is correct. The changes address directory fragmentation and specific weak boundaries; remaining routing errors should be handled as editorial corrections, not concealed by narrower names.
