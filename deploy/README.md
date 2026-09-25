# Deployment

For a database-free public site, run `python jobs.py --publish dist` on your PC/cluster and upload `dist/` to static hosting. See [the static publishing guide](../README.md#publish-a-static-site) for previewing, publication order, caching, and retention. The worker keeps SQLite and credentials locally; the website serves only HTML, JavaScript, and public JSON. Static mode includes digest previews but disables newsletter signup.

The setup below is the alternative for running the API and newsletter endpoints on a VM.

## API and worker on one VM

Run one VM with one local SQLite database. The web service never executes background work. A systemd timer starts one weekly job. Caddy terminates HTTPS and proxies to Uvicorn on loopback. This avoids a managed database, message queue, container registry, and platform rewrite.

## Suggested host and cost

For a $0 trial, an [Oracle Always Free A1 VM](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) can run this same setup with up to 2 OCPU, 12 GB RAM and 200 GB block storage. Check ARM64 dependency wheels during setup. Free A1 capacity can be unavailable and idle instances can be reclaimed, so keep an off-VM database backup. For a predictable HN launch, use Hetzner if Oracle has no capacity or the free-tier risk is unacceptable.

Start with a Hetzner CX23 in Germany or Finland if available: 2 shared vCPUs, 4 GB RAM and 40 GB NVMe. Its published September 2026 price is €5.49/month before VAT and IPv4. Daily server backups add 20%, about €1.10/month, for seven slots. The 5+ GB SQLite file fits comfortably because the 12 GB dump stays off the VM. If the weekly job runs out of memory or CX23 is unavailable, move to CX33 (8 GB RAM, 80 GB disk, €8.49/month before VAT and IPv4). A domain and transactional email are separate. Resend's free plan currently includes 3,000 emails/month, subject to its daily limit. Current prices and limits: [Hetzner](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/), [Hetzner backups](https://docs.hetzner.com/cloud/billing/faq/), [Resend](https://resend.com/pricing/). Check availability in the console before committing to a specific VM size.

Hetzner blocks outbound mail ports 25 and 465 by default; authenticated SMTP on 587 works. See [their FAQ](https://docs.hetzner.com/cloud/servers/faq/). Set `SMTP_HOST=smtp.resend.com`, `SMTP_PORT=587`, `SMTP_USER=resend`, and `SMTP_PASSWORD` to a dedicated API key after verifying the sending domain.

## Provision

1. Create the VM with SSH-key login, a public IP, and a firewall allowing inbound 22, 80 and 443. Create the DNS A record. Install Python 3.12+, `python3-venv`, and [Caddy](https://caddyserver.com/docs/install).
2. Create a non-root `hn-topics` user and `/srv/hn-topics/{app,data}` owned by it. Transfer the repository contents to `app`, excluding `.env`, `.venv`, `.git`, logs, local database, and the Parquet dump. On the VM, run `python3 -m venv .venv` and `.venv/bin/pip install -r requirements.txt` inside `app`.
3. Create a consistent local snapshot with `python scripts/snapshot.py data/hackernews.db /tmp/hn-topics-snapshot.db`. Transfer that snapshot to `/srv/hn-topics/data/hackernews.db`. Do not use `cp` or `rsync` directly on the live database. The app also needs `data/topic_model.npz` from the repository.
4. Copy `.env.example` to `/etc/hn-topics.env` and fill `HN_DB`, `PUBLIC_URL`, `OPENAI_API_KEY`, and SMTP settings. Keep that file root-owned with mode 600. `PUBLIC_URL` must be the final HTTPS origin.
5. Replace the hostname in `deploy/Caddyfile`, install it as `/etc/caddy/Caddyfile`, and enable Caddy. [Caddy obtains and renews TLS automatically](https://caddyserver.com/docs/automatic-https).
6. Install `deploy/*.service` and `deploy/*.timer` in `/etc/systemd/system/`. Run `systemctl daemon-reload`, enable `hn-topics-web.service` and `hn-topics-jobs.timer`, then start `hn-topics-jobs.service` once. Verify `/health`, the map, the digest preview and a real confirmation email. Inspect `journalctl -u hn-topics-jobs.service` for worker errors.
7. Enable Hetzner's daily server backups and restore a backup to a spare VM once before launch. Backups include the local disk; keep the database on that disk, rather than an attached volume. [Hetzner backups exclude attached volumes](https://docs.hetzner.com/cloud/servers/backups-snapshots/overview/).

The timer runs at 03:00 UTC with a small random delay and catches up after downtime. Keep a single enabled timer. Update code, install any changed requirements, restart the web service and check the next job's log. SQLite WAL files stay next to the database. The web and worker must use the same `HN_DB` path.

Cloudflare Workers and D1 are attractive for a small read-only site, but this 5+ GB Python/NumPy/SQLite app would require a rewrite and a separate ingestion service. D1's free per-database cap is 500 MB and its paid cap is 10 GB [per current limits](https://developers.cloudflare.com/d1/platform/limits/). Cloud Run's local disk is [ephemeral](https://docs.cloud.google.com/run/docs/configuring/services/ephemeral-disk), so it would also require external persistent storage and a scheduled job. The single VM is less work and likely cheaper at this scale.
