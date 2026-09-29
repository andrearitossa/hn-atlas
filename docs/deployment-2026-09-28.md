# Production rollout — 28 September 2026

Cloudflare Pages, D1 and the email sender are deployed. Daily and weekly local
systemd timers are enabled and active. Windows tasks start Ubuntu WSL at the
scheduled times and at logon. Monthly discovery is excluded from these scheduled
jobs with `--skip-discovery` / `discover_topics=False`.

| Job | Stockholm schedule | Entry point |
| --- | --- | --- |
| Daily | 06:30 | `scripts/update_site.sh` |
| Weekly | Sunday 18:00 | `scripts/weekly_newsletters.py` |

## Verified production results

- Daily refresh completed and filed 8,300 articles. HN fetches and embeddings
  remained committed when an initial classification attempt hit an API rate limit.
- The completed export is live at `https://hackeratlas.com`, with 206 topics.
  Pages deployment: `https://e412ba94.hackeratlas.pages.dev`.
- Sender version: `1dae9166-6e2d-4193-b3b3-3f2191b6ed2b`.
- Weekly preparation created 15 shared topic issues and sent 17 newsletters.
  Repeating that edition returned `sent=0, failures=0, pending=0`.
- Migrations 0003–0005 are applied. No production table contains `_test_`.
  Production contains 20 subscriptions, 26 issues, 28 delivery records,
  zero recorded clicks at verification, and 4 feedback records.
- Migration preserved all subscription identities, cancellations, 11 historical
  sends and legacy unsubscribe tokens. Existing and legacy unsubscribe links
  were checked without cancelling real subscriptions. No test subscribers were added.
- The sender URL retains its existing `hackeratlas-newsletter-test` hostname
  to preserve links in previously sent messages; it uses production tables.

## Reliability changes

- Weekly preparation waits for the database lock and refreshes stale data before
  proceeding. It does not depend on website deployment.
- OAuth tokens come through Wrangler's refresh-capable authentication command.
  The weekly service includes the installed Node runtime in its PATH.
- Large classification packets are split by input size; rate-limit retries wait
  at least 30 seconds and honor the server's retry delay. Recent fetches can be
  reused for one hour after a downstream failure.
- Daily exports and uploads use separate temporary directories. An observed
  concurrent preview build replaced `pages-dist` during upload, so deployment no
  longer reads that mutable shared directory.
- Wrangler returned zero after that failed upload. `scripts/deploy_site.sh` now
  also requires an explicit deployment-complete acknowledgement before the daily
  workflow writes its success marker. The incorrect marker was removed and the
  publication retried successfully.

## Validation and recovery material

113 Python tests and 22 JavaScript tests passed, including migration preservation,
duplicate-send prevention, lock ordering, rate-limit recovery and upload races.
Live checks covered the site's release assets and topic data, an existing signup
without inserting another record, unsubscribe confirmation, sender authentication,
and repeat-edition delivery.

The pre-migration D1 export and migration rehearsal are private, ignored files in
`.wrangler/rollout-backup/`. Do not publish that directory. The final aggregate
verification is recorded there as `final-verification.json`.

Inspect future runs with:

```bash
systemctl --user list-timers hackeratlas-daily.timer hackeratlas-weekly.timer
journalctl --user -u hackeratlas-daily.service -u hackeratlas-weekly.service
.venv/bin/python scripts/pipeline_status.py
```

Newsletter source-page failures use the existing HN-body/empty-overview fallback;
some external sites returned 403, 404 or unreadable pages during preparation.
