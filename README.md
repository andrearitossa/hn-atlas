# HN Atlas

Explore Hacker News through a stable map of topics, then subscribe to a weekly or monthly digest of the stories that matter to you.

Run ingestion and topic maintenance on your PC or cluster, then publish HTML and JSON to static hosting. The public site needs no Python server, database, or API key. The existing FastAPI server is also available for live browsing and newsletters. Python 3.12 or newer is required for the private pipeline. On Debian/Ubuntu, install the matching `python3-venv` package if `python3 -m venv` reports that `ensurepip` is unavailable.

## Publish a static site

After installing dependencies and obtaining the database as described below:

```bash
# Export existing data; no ingestion, paid API calls, or email delivery.
.venv/bin/python publish.py --output dist

# Preview the exported site with a plain file server.
python3 -m http.server 8080 --bind 127.0.0.1 --directory dist
```

Open <http://localhost:8080>. For generic static hosting, upload `dist/`; for Cloudflare Pages, use the smaller bundle below. Relative asset URLs and hash routes also support hosting below a path such as `/hn-atlas/`.

## Cloudflare Pages

The public site can be hosted on Cloudflare Pages using **Direct Upload**. The private Python worker and SQLite database stay on your PC or cluster. A Git push alone will not update the site: `dist/` is ignored by Git, and Pages cannot rebuild it from source without your local database.

After each successful export, prepare a folder containing only the current release:

```bash
python3 scripts/prepare_pages.py
```

Deploy with Wrangler from the repository root so it includes `functions/` and the D1 binding in `wrangler.jsonc`. Dashboard drag-and-drop uploads do not deploy the signup function. After logging in, run:

```bash
npx wrangler d1 migrations apply NEWSLETTER_DB --remote
npx wrangler pages deploy pages-dist --project-name hackeratlas --branch main
```

Upload the **folder**, not the repository or the whole accumulated `dist/`. The Pages bundle is replaced on each preparation, so repeated local exports do not increase its file count. The site keeps topic browsing, search, maps, and digest previews.

### Topic newsletter interest

The Pages build enables a newsletter signup on each topic page. `POST /api/newsletter/subscribe` saves the normalized email, topic ID, and topic name in the private `hackeratlas-newsletter` D1 database. The topic is validated against the deployed catalog; repeated signups for the same email/topic update the name without creating duplicate rows. No emails, credentials, or signup records are exposed in the static bundle or a public read endpoint. The form includes explicit email-update consent, a honeypot, pending/error/success states, and describes the newsletter as an early signup. The endpoint also checks request origin and bounds request size. The honeypot is basic spam protection, not email ownership verification.

This collects interest only: no confirmation or newsletter emails are sent. Add confirmation, delivery, and unsubscribe support before starting automated mail. The existing Python SMTP subscription system is separate. Plain `dist/` exports keep signup disabled; `scripts/prepare_pages.py` enables it specifically for the Pages deployment.

To see interest by topic without exporting email addresses:

```bash
npx wrangler d1 execute NEWSLETTER_DB --remote --command 'SELECT topic_id, topic_name, COUNT(*) AS signups FROM newsletter_signups GROUP BY topic_id, topic_name ORDER BY signups DESC'
```

Subscriber records can be managed privately through the Cloudflare D1 console. Keep any exports outside the public build folders. `wrangler.jsonc` records the production database binding; use a separate D1 database if creating an independent preview environment.

For weekly updates on your PC/cluster, load your environment and schedule:

```bash
.venv/bin/python jobs.py --publish dist
```

This updates the private database, exports after a successful update, and retains the existing newsletter delivery behavior when SMTP is configured. Run only one worker/publisher at a time. Upload the successful export afterward using your host's deployment tool. When your PC is offline, the published site remains available with its last snapshot.

Each export contains the topic map, summaries, activity charts, digest previews, and one story file per topic. The browser loads stories on demand and handles title/URL search, sorting, period filters, and pagination locally. Merged topic links keep working. Cloudflare Pages collects newsletter interest in D1; email delivery still requires a separate newsletter service.

Builds read one consistent SQLite snapshot and publish a versioned `dist/releases/<id>/` directory. The entry point, `dist/index.html`, switches only after the build succeeds. Deploy complete snapshots atomically when your host supports it; otherwise upload the new release directory first and `index.html` last. Revalidate `index.html` on each visit; versioned release files can be cached indefinitely. Keep previous release directories for existing tabs and cached entry points. They accumulate, so archive/remove older releases according to your retention window; open tabs using a removed release must reload. A fresh output directory creates a single-release deployment.

No embeddings, database files, subscriber details, confirmation tokens, or environment secrets are exported. The database and `data/topic_model.npz` remain on your worker. Node.js is only used by the optional browser-data parity tests, not to build or serve the site.

## Run locally

```bash
git clone <this-repository-url>
cd hn-atlas
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p data
```

Put a **matching database snapshot** at `data/hackernews.db`. The database is intentionally excluded from Git because it is over 5 GB. The included `data/topic_model.npz` holds the original 150 topic centroids; the database holds the current registry, topic names, assignments, and newsletter state. A copy of the creator's database is required to reproduce the current live map exactly. Create a consistent copy of a running database with:

```bash
python scripts/snapshot.py data/hackernews.db /path/to/hackernews-snapshot.db
```

Copy that snapshot to `data/hackernews.db` on the new machine. Then start the app:

```bash
python -m unittest discover -s tests
uvicorn server:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Each topic includes story search, sorting, period filters, and pagination. `/health` checks the runtime tables and reports `latest_story_at` and `stale` (no live stories within ten days, allowing for the weekly cadence); an unavailable or uninitialized database returns HTTP 503. The web server sends signup confirmations; scheduled ingestion and digest delivery run separately.

## Timeline

Each topic contains a continuous, zoomable timeline. The default view starts with the newest year and shows up to two standout articles per year. Scroll down to travel into the past; every zoom level and its articles run newest first. Use +/− or the keyboard-accessible detail slider to expand to five per year, then three per month and three per week. Clicking a period heading zooms into that period. Scroll within the timeline to travel through the archive; a year navigator and reset control provide quick orientation. Zooming keeps a visible article anchored when it remains a pick, or preserves its calendar period otherwise.

All resolutions cover the full available topic archive, including quiet periods. Picks are ranked by snapshot HN points, then comments and ID, deduplicated by article link within each period, and displayed newest first. A minimum of 10 points keeps the selection focused. The two annual highlights are a subset of the five annual picks. HN popularity is a discovery signal, not proof of lasting impact; the archive may not include a field’s beginnings. Dates use UTC, weeks start on Monday, and first/last periods can be incomplete.

`GET /api/topics/{id}/timeline?view=zoom` returns year, month, and week resolutions and follows merged topic IDs. The shared `timeline.py` read model powers live browsing and static exports with no paid model calls. Compact selections for every resolution are exported under the `zoom` key in each topic timeline file. The earlier yearly history and 30/90/365-day API windows remain available for compatibility. Republish an existing static site to include the new selections.

## Browse the API

`GET /api/topics/{id}/stories?q=rust&sort=newest&days=30&limit=20&offset=0`

- `q`: title or website substring, up to 200 characters. `%` and `_` are literal characters.
- `sort`: `newest` (API default), `top`, or `discussed`. The topic page starts with the highest rated stories from the last 30 days.
- `days`: time window; `0` (default) includes all history.
- `year`: UTC calendar year; `0` (default) includes all years. Combines with search and period filters. Timeline chapters link to their year in the archive.
- `limit`: 1–100 stories, default 20. Pass the response's `next_offset` to load another page; `null` means the end. Ordering is deterministic for an unchanged database; ingestion can shift offsets.

Topic responses, story results, and digest previews include `as_of`, a Unix timestamp for the newest live story in the snapshot. Browsing windows and digest previews use that date so historical snapshots stay explorable; outgoing emails use the actual current date. Deleted and dead stories are excluded. Old topic IDs follow successive merges automatically.

`GET /api/topics/{id}/digest?cadence=weekly` previews up to eight picks without SMTP; `monthly` covers 30 days.

## Rebuild from public data

If no snapshot is available, you can create a **new** map from the [open-index/hacker-news Parquet archive](https://huggingface.co/datasets/open-index/hacker-news). This requires more than 12 GB for the download, additional space for SQLite, and an OpenAI API key for embeddings and topic names. The archive changes over time, so a new build will not have identical topic IDs, names, or assignments. Do not use this path to replace an existing registry with subscribers.

```bash
python -m pip install -r requirements-import.txt
hf download open-index/hacker-news --repo-type dataset \
  --include 'data/*/*.parquet' --local-dir data/hn_dump
python hn_import_dump.py --dump data/hn_dump --db data/new-map.db
export OPENAI_API_KEY='your-key'
python embed.py --db data/new-map.db --version 2
python topics.py --db data/new-map.db --model data/new-topic-model.npz
export HN_DB=data/new-map.db
export HN_TOPIC_MODEL=data/new-topic-model.npz
python jobs.py
```

The import retains stories, polls, and jobs, skipping comments. `embed.py` processes live posts from 2024 onward. `topics.py` selects a topic count using a chronological holdout, validates subject coherence, and consolidates synonymous subjects; `jobs.py` then catches up with current HN items and maintains the registry. The first build can take substantial time and paid API calls. The scheduled worker should run once weekly after initialization, never concurrently with itself.


## Topic pipeline

Initial builds run in a separate database and refuse to overwrite a populated registry or an existing model artifact. V2 embedding inputs remove Show/Ask/Tell/Launch HN prefixes and trailing format/year markers; format and source are stored separately. External-link submission commentary does not change the article's subject. Identical inputs reuse embeddings. Training deduplicates inputs, holds out the newest 20%, and compares 60/90/120/150 candidate clusters; the smallest model within 2% of the best held-out fit is preferred, with fewer near-duplicate centers taking priority. This heuristic measures geometric coverage, not semantic accuracy. Candidate subjects require at least 40 sampled supporting stories. Naming examines representative and varied stories, rejects format/noise clusters, and consolidates redundant subject definitions before publishing. `--candidates` and `--sample` allow bounded experimental builds.

The backend wakes once weekly. Run `python jobs.py --publish dist`; the supplied timer runs Monday at 03:00 UTC. A worker lock prevents overlapping jobs, and a calendar-week checkpoint prevents accidental duplicate ingestion. Failed stages can retry; embeddings, reviews, and topic maintenance retain their completed work.

1. Catch up new HN items and refresh points, comment counts, edits and deletion flags for stories from the last 14 days. Stories fetched during catch-up are not fetched again. Older counts stay at their last fetched values. Score-only changes reuse embeddings; changed subject content invalidates them.
2. Embed new/changed inputs and classify by similarity. Confident assignments publish immediately, including popular stories. Weak fits (below 0.2862) or ambiguous margins (below 0.02) wait for one weekly semantic request using GPT-6 Luna covering up to 100 recent uncertain stories. Canonical duplicates share decisions. The worker retries only recent uncertainty, at most three semantic attempts; it does not rescan years of unresolved stories every week. Missing API credentials defer reviews. Existing registries retain their original embedding-input version; v2 needs a separate matching build.
3. Maintain topics using the last 28 days. Centers drift only from confident agreeing stories. New subjects require 40 distinct articles, three sources, support across weeks, separation, and two weekly observations. At most three candidates receive naming/distinction checks each week, combining those checks in one request per candidate. Merges require near-duplicate centers and substantial independent article evidence. Splits require two coherent separated groups, each with sustained cross-source support. One combined semantic request validates at most three merge and three split proposals; only approved changes apply. Reviewed topics cool down for 28 days. Only affected assignments are recomputed; the entire archive is not reclustered.

Old topic URLs resolve through merge aliases. Subscriptions keep their tokens and follow merged destinations; duplicate subscriptions to the same resulting subject receive one digest. On splits, the larger child keeps the existing ID and subscriptions; no subscriptions are copied. Applied merges/splits are recorded in `topic_changes`. Separate report-only quality audits and popularity-triggered reviews are no longer scheduled. In a normal week this is at most five semantic requests (one assignment review, three new-subject checks, one structural review), often fewer; retries and initial builds are additional.

Inspect deferred work without running the pipeline:

```bash
python scripts/pipeline_status.py --db data/hackernews.db
```

The new pipeline takes effect on subsequent worker runs. It does not retrospectively rebuild the live map. A fresh map has new IDs and must not replace a registry with subscribers without a separate migration. An API/model failure leaves committed ingestion and pending reviews available for retry.

For a read-only, reproducible clustering comparison on a bounded public-story sample:

```bash
python scripts/evaluate_pipeline.py --db data/hackernews.db --embed
# Later runs reuse the experiment cache without embedding API calls:
python scripts/evaluate_pipeline.py --db data/hackernews.db
```

`--embed` authorizes embedding up to 20,000 public story inputs when no cache exists. Outputs are under `report/pipeline-evaluation/`. `python scripts/evaluate_routing.py` scores the targeted 15-story regression fixture; add `--review` for a bounded semantic-model comparison. Topic naming, semantic review, and new-subject validation also use paid model calls; no API calls occur in the public read API or static publisher.

## Email and configuration

Copy `.env.example` to `.env` and set the matching `HN_DB` and `HN_TOPIC_MODEL`, `PUBLIC_URL`, `OPENAI_API_KEY`, and SMTP settings for a live worker. For a local shell, load it before launching processes:

```bash
set -a
. ./.env
set +a
```

Digest preview works without SMTP. To send confirmation and digest messages, set `PUBLIC_URL` to the public HTTPS origin and provide `SMTP_HOST`, `SMTP_FROM`, plus credentials if your provider requires them. Run `python jobs.py` once per day from the project directory. Keep `.env` out of Git.

## Project layout

- `index.html` and `data.js`: interface and live/static data adapter.
- `publish.py` and `catalog.py`: static publisher and shared public read models.
- `server.py`: optional public API and newsletter endpoints.
- `jobs.py`, `weekly.py`, `hn_sync.py`, `embed.py`: scheduled updates (`daily.py` is a compatibility entry point).
- `production.py`, `quality.py`, `pages.py`, `core.py`: topic registry, digest, quality checks, and shared math.
- `hn_import_dump.py`, `topics.py`: one-time historical import and model build.
- `data/topic_model.npz`: small initial model; `data/hackernews.db` and `data/hn_dump/` are local data and are Git-ignored.
- `deploy/`: example single-VM service and HTTPS setup.

See [deploy/README.md](deploy/README.md) for a production deployment. The repository is MIT licensed; the upstream HN data remains governed by its own [dataset terms](https://huggingface.co/datasets/open-index/hacker-news).

### Visitor feedback

The footer sits at the end of each page’s content and shows “Built by Andrea Ritossa · Feedback”. On Cloudflare Pages, “Leave feedback” opens a dialog with a required message (up to 2,000 characters) and an optional reply email. `POST /api/feedback` stores the message, optional email, page route, timestamp, and retry ID in the private D1 `feedback` table. It does not subscribe the sender or send email. The endpoint validates requests, limits body size, checks origin, and includes a honeypot; repeated submissions with the same ID do not create duplicate records. There is no public read endpoint.

Apply migrations before deploying as above. Read feedback privately in the Cloudflare D1 console, or run:

```bash
npx wrangler d1 execute NEWSLETTER_DB --remote --command 'SELECT message, email, page, created_at FROM feedback ORDER BY created_at DESC LIMIT 100'
```
