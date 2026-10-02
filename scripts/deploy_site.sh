#!/bin/bash
# Upload an isolated bundle, so another local build cannot replace its files.
set -euo pipefail
[[ $# -le 1 ]] || { echo "Usage: $0 [EXPORT_DIRECTORY]" >&2; exit 2; }
cd "$(dirname "$0")/.."
if [[ -f functions/api/search/index.js && ! -f search-cloudflare.json ]]; then
  echo 'Search backend is not configured. Run scripts/setup_search.py and search_sync.py before deploying.' >&2
  exit 1
fi
if [[ -f functions/api/search/index.js ]]; then
  .venv/bin/python scripts/check_search_ready.py
  .venv/bin/python scripts/migrate_search_history.py
fi
export PATH="$HOME/.local/share/pi-node/node-v22.23.1-linux-x64/bin:$PATH"
mkdir -p .wrangler
deployment=$(mktemp -d "$PWD/.wrangler/pages-deploy.XXXXXX")
trap 'rm -rf "$deployment"' EXIT
.venv/bin/python scripts/prepare_pages.py --source "${1:-dist}" --output "$deployment/site"
# Fail closed: exercise this exact upload bundle before publishing anything.
npm test
SITE_BUNDLE="$deployment/site" npm run test:usability
./node_modules/.bin/wrangler pages deploy "$deployment/site" --project-name hackeratlas --branch main --commit-dirty=true 2>&1 | tee "$deployment/deploy.log"
# Wrangler can return zero after an upload error (observed with ENOENT).
if ! grep -Fq 'Deployment complete!' "$deployment/deploy.log"; then
  echo 'Pages did not confirm a completed deployment; publication failed.' >&2
  exit 1
fi
