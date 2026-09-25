# Editorial curation review

The 287-topic static candidate was manually curated into **206 subscriptions**. There was no target count: related subscriptions were combined, misleading labels broadened, and unreliable boundaries removed. Every source topic has a recorded destination in `curation.json`.

- Fifteen source centers are excluded from routing, including brittle pandemic slices, the ambiguous investment-portfolio bucket, and vague machines/futures/scarcity themes.
- Five boundaries are rebuilt from manually selected examples: literature, documents/spreadsheets, personal finance, data visualization, and systems programming.
- Seventeen individual story errors are corrected explicitly, retaining actual vector fit scores in the database and recording the editorial decision separately.
- The existing 116 public topic URLs redirect to related curated subscriptions. Old topic IDs are not reused for unrelated subjects.
- The source corpus, earlier static map, and uncurated 287-topic model are retained. No subscriber records are rewritten and no newsletter is sent.

## Validation

The final same-story, model-assisted comparison covers 210 stories: the curated map gives 131 good assignments and 16 clear wrong assignments (169 routed), versus 124 good and 20 wrong (164 routed) for the raw 287-topic candidate. The other routed stories were judged plausible/weak. This is a modest directional improvement, not proof of an optimal taxonomy. Exact counts, the paired bootstrap interval, and limitations are in `validation.json`.

Every curated subscription received an eight-title diagnostic audit. Mean coherence was 4.07/5 and mean usefulness 4.04/5. Five samples were still flagged as weak: consciousness/perception, mobile/device security, Iran/regional conflict, wellness tracking, and personal computers. These are remaining editorial follow-up areas; curation does not establish perfect filtering or unattended production quality. The live baseline also had routing errors, so neither its topic count nor this one is ground truth.

The database is rebuilt across the complete stored 2020–September 25, 2026 corpus. Classifier tests check merged-center ambiguity, stable noncontiguous IDs, plan integrity, and explicit editorial corrections. Static-publication and public API regression tests also pass.
