#!/bin/bash
# One daily corpus update, followed by an atomic export and deployment.
set -euo pipefail
[[ $# -eq 0 || ( $# -eq 1 && "$1" == --force ) ]] || {
  echo "Usage: $0 [--force]" >&2
  exit 2
}
cd "$(dirname "$0")/.."
source scripts/node_env.sh
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
exec 9>data/update-site.lock
flock -n 9
today=$(TZ=Europe/Stockholm date +%F)
last_success=$(cat data/update-site-success.date 2>/dev/null || true)
if [[ "${1:-}" != --force && "$last_success" == "$today" ]]; then
  echo "Site already published on $today (Europe/Stockholm)"
  .venv/bin/python -u scripts/daily_selection.py
  exit 0
fi
if [[ "${1:-}" != --force && "$last_success" == "$(TZ=Europe/Stockholm date -d yesterday +%F)" && "$(TZ=Europe/Stockholm date +%H%M)" < 0630 ]]; then
  echo "Yesterday's site is current; waiting for the 06:30 Stockholm run"
  exit 0
fi
# WSL often starts before Windows networking is ready; wait up to 10 minutes.
for _ in $(seq 120); do
  getent hosts hacker-news.firebaseio.com api.cloudflare.com >/dev/null && break
  sleep 5
done
mkdir -p .wrangler
build=$(mktemp -d "$PWD/.wrangler/daily-build.XXXXXX")
trap 'rm -rf "$build"' EXIT
.venv/bin/python -u refresh.py --skip-discovery --publish "$build/dist"
bash scripts/deploy_site.sh "$build/dist"
if [[ -f search-cloudflare.json ]]; then
  .venv/bin/python -u search_sync.py
fi
# Retain the exact deployed export locally. Copy assets first, then atomically
# switch the index; keep older releases for tabs that still reference them.
mv "$build/dist/index.html" "$build/index.html"
mkdir -p dist
cp -a "$build/dist/." dist/
mv "$build/index.html" dist/index.html
# Keep the live release plus the two newest previous ones for open tabs.
current=$(sed -n 's|.*releases/\([0-9a-f]\{32\}\)/.*|\1|p' dist/index.html | sed -n '1p')
if [[ -n "$current" && -d "dist/releases/$current" ]]; then
  ls -t dist/releases | awk -v current="$current" 'length($0)==32 && /^[0-9a-f]+$/ && $0!=current' | tail -n +3 |
    while read -r old; do rm -rf "dist/releases/$old"; done
fi
# Written only after the update, deployment, and local synchronization succeed.
TZ=Europe/Stockholm date +%F > data/update-site-success.tmp
mv data/update-site-success.tmp data/update-site-success.date
.venv/bin/python -u scripts/daily_selection.py
