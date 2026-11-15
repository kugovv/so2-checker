#!/bin/sh
set -eu
umask 077
ulimit -v 131072
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/opt/so2-check/game-client
exec /usr/bin/flock -n -E 75 /var/lib/so2-check/game-client.lock \
  /opt/so2-check/game-client-venv/bin/python -m game_client "$@"
