# Hacker Atlas

A curated map of Hacker News topics, with a daily data update and a static website.

## One database, one pipeline

- `data/atlas.db` is the only local story database. It contains stories, embeddings,
  206 curated topics, editorial corrections, and update checkpoints.
- `data/topic_model.npz` contains the matching curated vector model.
- `config.py` owns these paths and loads API credentials from `.env`.
- `refresh.py` is the only ingestion and topic-maintenance pipeline.

The archive includes stories from 2006 onward. The curated map was built over the
2020–2026 corpus; older preserved stories are not automatically assigned to this
map. Historic topic IDs and their incompatible assignments are not imported.

## Update and publish

Use Python 3.12+ and install `requirements.txt` in `.venv`. Configure
`OPENAI_API_KEY` in `.env` (see `.env.example`). The private database and model
must exist; an empty database is not a working topic map.

```bash
# Update the database and export the site locally.
.venv/bin/python refresh.py --publish dist

# Inspect the successful-run checkpoint and current backlog.
.venv/bin/python scripts/pipeline_status.py

# Update, export, prepare the public bundle, and deploy to Cloudflare Pages.
bash scripts/update_site.sh
```

Install the user timers with `bash ops/install_user_timers.sh` from this checkout.
They run the daily update at **06:30 Europe/Stockholm** and prepare and send weekly
newsletters on Sundays at **18:00**. `Persistent=true` runs one missed timer
event when the user manager starts after the PC was off; `refresh.py` catches up
all HN items since its checkpoint and refreshes the recent story window. The
installer enables linger when permitted so timers can run before login. A failed
run is retried twice at 15-minute intervals. Inspect results with
`systemctl --user list-timers` and `journalctl --user -u hackeratlas-daily.service
-u hackeratlas-weekly.service`.

On WSL2, Windows must start the distro before its user timers can run. After
installing the Linux timers, run the Windows task installer from a Windows
PowerShell prompt for your account:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\\wsl.localhost\Ubuntu\home\andrea\technews\ops\install_windows_tasks.ps1" -Distro Ubuntu -LinuxUser andrea
```

Use your distro and Linux username if different. The installer checks that the
Windows time zone is Stockholm and creates logon, daily 06:30, and Sunday 18:00
triggers. Each task keeps WSL running until its service completes. Systemd's
daily success marker and newsletter delivery records prevent duplicate work after logon. Inspect
them with `Get-ScheduledTask -TaskName 'HackerAtlas-*-WSL'` in PowerShell.
An early logon waits for 06:30 if yesterday's publication succeeded; after a
longer outage, it starts catch-up immediately.

The weekly job waits for the shared database lock and refreshes the corpus itself
if the successful refresh checkpoint is missing or more than 36 hours old. This
handles startup after an outage without depending on a website deployment. It
computes one HTML newsletter per subscribed topic, stores it in D1, and
immediately calls the Cloudflare Worker to send that edition. An existing topic/edition
is reused, never recomputed per recipient. Preview reads the same stored HTML.
The Worker has no cron schedule, topic selection, or rendering logic.
Website deployment is independent. Missed newsletters catch up on Monday or later,
using the most recent Sunday 18:00 edition. The job waits for the database lock.
The installer retires the old newsletter-test cron entry.
Use `bash ops/install_user_timers.sh --install-only` during a rollout to install
and enable the units without starting jobs. After validation, start both timers
with `systemctl --user start hackeratlas-daily.timer hackeratlas-weekly.timer`.
The daily job includes monthly map maintenance. The weekly newsletter catch-up
refreshes existing topics without running monthly maintenance.
Install the pinned deployment tool with `npm ci`. Deployment needs Node and
authenticated Wrangler (`./node_modules/.bin/wrangler login`).
Install the browser once with `npx playwright install --with-deps chromium`.
Every website deployment runs the JavaScript tests and desktop/mobile Chromium
usability checks against the isolated upload bundle before uploading it. A failed
check stops publication. Checks cover topic search/navigation, story filters,
keyboard interaction, horizontal overflow, browser errors, and missing assets.
Run them locally with `SITE_BUNDLE=pages-dist npm run test:usability` after preparing
a bundle. Failure screenshots and traces are saved in `test-results/`.
CI runs the same browser checks against a small generated export. These checks
serve local static files; API behavior is covered by the JavaScript tests.
Git pushes do not update the public data.
Daily exports and Pages uploads use private temporary directories under `.wrangler/`,
so concurrent local preview builds cannot replace files during an upload.
After a successful daily deployment, the same export is copied into local `dist/`,
with the index switched last and previous releases retained for open tabs.
`scripts/deploy_site.sh` also requires Wrangler's explicit deployment confirmation
before the daily workflow records success. To publish an existing local export
without repeating ingestion, run `bash scripts/deploy_site.sh dist`.

The update steps are:

1. Fetch every HN item since the last completed catch-up. Save the starting
   checkpoint before writing chunks so an interrupted first run cannot skip IDs.
2. Refresh points, comments, edits, and deletion flags for the recent 60 days,
   plus older featured stories.
3. Reuse stored embeddings when the model, input version, and input hash match.
   Embed only missing or changed inputs. The input contains the normalized title
   and available **HN post text**, including posts with external URLs. HTML is
   stripped; body text is capped at 6,000 characters. Linked article pages and HN
   comment threads are not fetched for embeddings. Existing vectors are retained.
4. Classify new stories with the stored two-component Gaussian mixture per topic
   in `topic_classifier/models/model.npz`. Temperature-adjusted probabilities select the
   smallest ranked set reaching 70%, capped at three topics. Capped sets below 70%
   are recorded explicitly. Editorial corrections take priority. Existing assigned
   and queued stories are not bulk reclassified. Topic pages and newsletter
   candidates include secondary memberships; global timelines count a story once.
   See [experiments and production details](docs/topic-classification-experiments.md).
   Refit explicitly with `python -m topic_classifier.train`; daily runs only load
   the artifact. Retrain after changing active topic IDs.
5. Once per calendar month, check whether the map still reflects current HN discussions.
   Audit up to 12 recent stories per existing topic (popular, time-spread and weak
   matches). Independently reclassify flagged stories before moving them; explicit
   editorial corrections win. Store activity, sample counts and repairs in
   `topic_health`. Quiet topics are reported rather than automatically deleted.
   Then look for missing durable subjects among the last 30 days of unfiled posts,
   checked against the preceding 30 days and the size/coherence of existing topics.
   There is no requirement for three viral posts or evidence from a third month.
   The LLM checks scope and overlap; at most two new topics are added each month.
   Both phases and the monthly checkpoint commit together. Failures retry and
   missed months trigger one current assessment, not historical replays.
6. Export one consistent database snapshot, then deploy it. The local entry point
   switches only after the complete export succeeds.

Fetching and embedding commit incrementally. Topic filing and its successful-run
checkpoint commit together. Failed runs can retry. All writers share the database
worker lock; the daily update/deployment command also has an overall job lock.
A recent story timestamp alone does **not** prove that a refresh completed.

## Preview and browse

```bash
.venv/bin/python publish.py --output dist
python3 -m http.server 8080 --bind 127.0.0.1 --directory dist
```

To rebuild only the UI from the last exported database snapshot (no ingestion,
embeddings, or database queries):

```bash
.venv/bin/python publish.py --from-snapshot dist --output dist
.venv/bin/python scripts/prepare_pages.py
python3 -m http.server 8081 --bind 127.0.0.1 --directory pages-dist
```

“The conversation now” ranks stories by `(HN points - 1) * exp(-age_days / 3)`.
It requires at least five points, excludes dead/deleted and future posts, and
removes duplicate normalized URLs. Headline similarity has no effect. The page
shows the top five picks ordered newest to oldest by submission time. Snapshot rebuilds recalculate these picks from stored
stories without fetching new data.

Topic pages are complete static HTML. A small deferred `topic.js` attaches controls
without replacing the page or fetching startup data. Archive searches read year
shards; monthly and weekly timeline views are already rendered in the HTML and
switch without downloading or formatting stories. The connections page keeps its
interactive map and uses native links to topic documents. The shared stylesheet
and scripts live under immutable, versioned release URLs. Packaging validates
local script/style references before replacing `pages-dist`, and excludes build-only
whole-topic archives, timelines, and duplicate public HTML. Preview and packaging
do not deploy; the explicit Wrangler deploy command remains the final step.

The central page flows from Connections to Trends to Topics; `/#/trends` jumps
straight to the embedded treemap. It defaults to monthly counts and the leading
10% of topics, with 50% and 100% options. The selected topics fit within the
map without minimum tile dimensions; labels appear only when they fit. The coverage note reports the displayed count and share;
percentages always use all memberships, while tile areas fill the selected subset.
Weekly/monthly/yearly aggregation, smooth playback, topic history, comparisons,
and representative stories work from the HTML alone. The standalone `/analytics/`
view remains available. Selecting a topic opens a sidebar on desktop and a bottom
sheet on mobile. Full exports and snapshot rebuilds include both views.

The website provides a topic map, search, seven-day topic activity, current picks, archive
filters, and zoomable timelines. Static exports contain only public story/topic
information; no database, vectors, API credentials, or subscriber records.

For the optional live API:

```bash
.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000
```

Topic URLs use readable name slugs such as `/topic/llm-advances/`. Numeric topic
URLs redirect to their canonical slug; IDs remain the keys for API data. The exporter writes topic-specific HTML, canonical
URLs, Open Graph/Twitter metadata, a sitemap, and robots.txt. Topic content and
links are readable without JavaScript. Legacy `#/topic/<id>` links migrate in
the browser; client navigation and back/forward use the History API. The Pages
bundle includes these pages and a 404 page.

The API reads the same canonical database. `/health` reports data freshness.

## Newsletter and feedback

Cloudflare Pages stores newsletter interest and feedback in its remote D1
operational database. This is separate from the local story corpus. Keep
`functions/`, `migrations/`, `workers/`, and `.wrangler/`: they support deployed
features and delivery state.

Run `.venv/bin/python scripts/weekly_newsletters.py` to prepare and send in one
workflow, or add `--dry-run` to render HTML previews without writing to D1 or sending.
The delivery credential comes from `NEWSLETTER_ADMIN_TOKEN` or the existing private
`.wrangler/newsletter-admin-token`.

D1 has four newsletter tables:

- `newsletter_subscriptions`: one row per email/topic; `unsubscribed_at` marks inactive subscriptions.
- `newsletter_issues`: one HTML newsletter per topic/Sunday edition, plus `sent_count`.
- `newsletter_deliveries`: one small recipient/issue record to prevent duplicate sends after partial failures.
- `newsletter_clicks`: aggregate click count and last click time per topic/edition/story.

The preparation job writes the newsletter content. Only the Worker updates delivery
records and recomputes `sent_count` from successful sends. All readers of a topic
receive the same stored content, with their own unsubscribe link. There are no
custom topic names, stored summary notes, or separate plain-text renderer.

`migrations/0004_shared_newsletters.sql` consolidates the old signup/test tables,
preserves cancellations and old unsubscribe tokens, and imports delivery history.
It was applied to production on 28 September 2026 together with the matching
signup endpoint and Worker; migration 0005 adds aggregate click tracking.
Production has no legacy `_test_` tables. Nothing in the preparation job runs migrations.

Sunday 18:00 Stockholm is normally 09:00 San Francisco (10:00 during the brief
US/European daylight-saving mismatch). The local PC/WSL must be running for story
selection. The sender is deployed with delivery enabled and no Cloudflare cron;
only the authenticated local weekly workflow initiates delivery.

Newsletter preparation reads posts between consecutive Sunday 18:00 Stockholm boundaries.
It ranks all candidates by `0.8*log1p(upvotes) + 0.2*log1p(comments)`, without a
minimum score or topic-relevance weight. It deduplicates URLs/headlines within
that issue and keeps 15. Each page is fetched and gets a short grounded overview;
the LLM sees titles, post bodies, and those overviews to rank every candidate.
Code takes the first five (or all candidates when fewer than five exist).
The same overviews appear in the email. Selection errors are logged at ERROR and
fall back to the top five by engagement. Failed page fetches use the HN post body;
when neither source is available, the story has no generated overview.

Edit `prompts/newsletter-selection.txt` to change the editorial selection prompt
(currently a draft awaiting the owner's wording). No prior-issue story exclusion
is used. Ranking smoke comparisons: `docs/newsletter-scoring.md`.

## Code map

| Responsibility | Files |
| --- | --- |
| Configuration and connections | `config.py`, `database.py` |
| Fetch, embed, update | `refresh.py`, `hn_sync.py`, `embed.py`, `llm.py` |
| Topic model and routing | `topics.py`, `curation.py`, `routing.py`, `core.py` |
| Public read models | `catalog.py`, `attention.py`, `timeline.py` |
| Website and API | `index.html`, `data.js`, `topic.js`, `static_pages.py`, `server.py`, `publish.py` |
| Newsletter selection and delivery | `newsletter.py`, `scripts/weekly_newsletters.py`, `workers/newsletter-test/worker.mjs` |
| Operations | `scripts/update_site.sh`, `scripts/prepare_pages.py`, `scripts/pipeline_status.py`, `scripts/snapshot.py` |

Historical replays, alternative clustering builds, duplicate code snapshots,
and the legacy weekly pipeline have been removed. Tests use temporary databases.

```bash
.venv/bin/python -m unittest discover -s tests -v
node --test tests/*.test.mjs
```

Back up the database consistently with SQLite's backup API:

```bash
.venv/bin/python scripts/snapshot.py data/atlas.db /path/outside/this/repo/atlas-backup.db
```

The repository is MIT licensed. HN data retains its upstream terms.

### Newsletter article clicks

At send time, the Worker routes article title/source links through
`/click/{topic}/{edition}/{story}`. It looks up the destination in that stored
issue, increments its D1 counter, then returns a non-cacheable 302 redirect.
A recording failure is logged and still redirects. Unsubscribe and other links
remain direct. No recipient identifiers, IP addresses, or user agents are stored.
Counts measure GET requests (including repeat clicks and email scanners), not
unique human readers; HEAD requests redirect without counting.

`migrations/0005_newsletter_clicks.sql` adds the single counter table. Apply it
alongside the Worker when deployment is authorized; it has not been applied remotely.

### Checking topic-map relevance

Each experiment below has an 18-minute hard deadline (plus five seconds to stop).
Both read a consistent database snapshot; neither changes production data.
The first checks activity/backlog and deliberately hides Apple, Rust, and Robotics
from the candidate generator to measure whether their subjects can be recovered.
The second uses the configured OpenAI API on sampled public HN text and simulates
repairs in an in-memory database, with detailed local outputs for manual inspection.

```bash
timeout --kill-after=5s 1080s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/check_topic_map.py
timeout --kill-after=5s 1080s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/check_topic_map.py --llm
```

These are diagnostics, not taxonomy accuracy scores: existing assignments are an
imperfect reference, sampling emphasizes likely errors, and a recovered candidate
still needs independent evidence and editorial review before becoming a topic.

Feedback and new newsletter subscriptions notify `andre.ritossa@gmail.com` through
the existing email Worker's private `NotificationSender` entrypoint (the Pages
`NOTIFICATIONS` service binding). Signup emails include the catalog topic name and
subscriber email; feedback includes the message, optional email, and page. Only
new inserts notify, so retries do not send duplicate emails. Notification failures
are logged without failing a saved submission; there is no automatic mail retry.
Deploy `workers/newsletter-test/wrangler.jsonc` before deploying Pages when changing
this binding or entrypoint.
