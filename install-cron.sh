#!/usr/bin/env bash
# Install / remove the MaizeTix watcher as a WSL cron job.
#
#   ./install-cron.sh              install at the default 10-minute interval
#   ./install-cron.sh --every 5    install at a 5-minute interval
#   ./install-cron.sh --remove     take it back out
#   ./install-cron.sh --status     show whether it is installed
#
# Heads up: your user crontab is normally empty (per your global notes, the
# other recurring jobs run from Windows Task Scheduler). This adds a real WSL
# cron entry, which only fires while WSL is running. If you want it to survive
# WSL being shut down, drive it from Task Scheduler instead -- see CLAUDE.md.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKER="# maizetix-scraper"
INTERVAL=10
ACTION=install

while [[ $# -gt 0 ]]; do
  case "$1" in
    --every)  INTERVAL="$2"; shift 2 ;;
    --remove) ACTION=remove; shift ;;
    --status) ACTION=status; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

current() { crontab -l 2>/dev/null || true; }

case "$ACTION" in
  status)
    if current | grep -q "$MARKER"; then
      echo "installed:"; current | grep "$MARKER"
    else
      echo "not installed"
    fi
    ;;
  remove)
    (current | grep -v "$MARKER" || true) | crontab -
    echo "removed the MaizeTix cron entry"
    ;;
  install)
    if ! [[ "$INTERVAL" =~ ^[0-9]+$ ]] || (( INTERVAL < 1 || INTERVAL > 59 )); then
      echo "--every takes 1-59 minutes" >&2; exit 1
    fi
    LINE="*/$INTERVAL * * * * cd $DIR && /usr/bin/python3 main.py >> $DIR/logs/cron.log 2>&1 $MARKER"
    { (current | grep -v "$MARKER" || true); echo "$LINE"; } | crontab -
    echo "installed, running every $INTERVAL minute(s):"
    echo "  $LINE"
    echo
    echo "logs -> $DIR/logs/cron.log and $DIR/logs/scraper.log"
    ;;
esac
