#!/bin/bash
# One daily corpus update, followed by an atomic export and deployment.
set -euo pipefail
[[ $# -eq 0 || ( $# -eq 1 && "$1" == --force ) ]] || {
  echo "Usage: $0 [--force]" >&2
  exit 2
}
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/share/pi-node/node-v22.23.1-linux-x64/bin:$PATH"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
exec 9>data/update-site.lock
flock -n 9
today=$(TZ=Europe/Stockholm date +%F)
last_success=$(cat data/update-site-success.date 2>/dev/null || true)
if [[ "${1:-}" != --force && "$last_success" == "$today" ]]; then
  echo "Site already published on $today (Europe/Stockholm)"
  exit 0
fi
if [[ "${1:-}" != --force && "$last_success" == "$(TZ=Europe/Stockholm date -d yesterday +%F)" && "$(TZ=Europe/Stockholm date +%H%M)" < 0630 ]]; then
  echo "Yesterday's site is current; waiting for the 06:30 Stockholm run"
  exit 0
fi
mkdir -p .wrangler
build=$(mktemp -d "$PWD/.wrangler/daily-build.XXXXXX")
trap 'rm -rf "$build"' EXIT
.venv/bin/python -u refresh.py --skip-discovery --publish "$build/dist"
bash scripts/deploy_site.sh "$build/dist"
# Written only after the corpus update and public deployment both succeed.
TZ=Europe/Stockholm date +%F > data/update-site-success.tmp
mv data/update-site-success.tmp data/update-site-success.date
