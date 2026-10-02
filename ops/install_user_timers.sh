#!/bin/bash
# Run from this checkout after reviewing the units. No root access is required.
set -euo pipefail
[[ $# -eq 0 || ( $# -eq 1 && "$1" == --install-only ) ]] || {
  echo "Usage: $0 [--install-only]" >&2
  exit 2
}
repo=$(cd "$(dirname "$0")/.." && pwd)
[[ "$repo" == "$HOME/technews" ]] || {
  echo "Units expect the checkout at $HOME/technews; found $repo" >&2
  exit 1
}
units=(hackeratlas-daily.service hackeratlas-daily.timer hackeratlas-weekly.service hackeratlas-weekly.timer)
mkdir -p "$HOME/.config/systemd/user"
for unit in "${units[@]}"; do
  ln -sfn "$repo/ops/$unit" "$HOME/.config/systemd/user/$unit"
done
systemctl --user daemon-reload
systemctl --user enable hackeratlas-daily.timer hackeratlas-weekly.timer

# Retire only the bounded test cron entry; keep all unrelated jobs.
cron=$(mktemp)
cron_error=$(mktemp)
trap 'rm -f "$cron" "$cron.new" "$cron_error"' EXIT
if crontab -l > "$cron" 2>"$cron_error"; then
  if rg -q 'hackeratlas-newsletter-test-until-2026-10-17' "$cron"; then
    cp "$cron" "$repo/data/crontab-before-hackeratlas-timers.txt"
    rg -v 'hackeratlas-newsletter-test-until-2026-10-17' "$cron" > "$cron.new" || true
    crontab "$cron.new"
    echo "Retired the bounded newsletter-test cron entry; backup: data/crontab-before-hackeratlas-timers.txt"
  fi
elif ! rg -qi 'no crontab for' "$cron_error"; then
  cat "$cron_error" >&2
  echo "Could not inspect the existing crontab; check for a duplicate job." >&2
  exit 1
fi

if ! loginctl show-user "$USER" -p Linger 2>/dev/null | rg -q '^Linger=yes$'; then
  if loginctl enable-linger "$USER"; then
    echo "Enabled user-manager linger for runs before login."
  else
    echo "Could not enable linger. Ask an administrator to run: sudo loginctl enable-linger $USER" >&2
  fi
fi
systemctl --user list-timers hackeratlas-daily.timer hackeratlas-weekly.timer
if [[ "${1:-}" != --install-only ]]; then
  systemctl --user start hackeratlas-daily.timer hackeratlas-weekly.timer
  systemctl --user start --no-block hackeratlas-daily.service
fi
