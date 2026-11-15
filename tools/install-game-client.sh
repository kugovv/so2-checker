set -eu

# Additive installation only. Existing application services and DB are untouched.
archive=/var/tmp/so2-game-client-release.tar.gz
target=/opt/so2-check/game-client
venv=/opt/so2-check/game-client-venv
unit=/etc/systemd/system/so2-check-game-probe.service
test -f "$archive"
test ! -e "$target"
test ! -e "$venv"
test ! -e "$unit"
test ! -e /opt/so2-check/bin/game-client
id so2check >/dev/null

install -d -o root -g so2check -m 0750 "$target"
python3 - "$archive" "$target" <<'PY'
from pathlib import Path
import sys
import tarfile
archive, destination = map(Path, sys.argv[1:])
with tarfile.open(archive) as bundle:
    for member in bundle.getmembers():
        if not member.isfile() or not member.name.startswith(('game_client/', 'deployment/', 'tests/')):
            raise SystemExit('Unexpected archive member')
        resolved = (destination / member.name).resolve()
        if not resolved.is_relative_to(destination.resolve()):
            raise SystemExit('Archive member outside destination')
    bundle.extractall(destination, filter='data')
PY

python3 -m venv "$venv"
"$venv/bin/pip" install --disable-pip-version-check --no-cache-dir \
  -r "$target/game_client/requirements.txt" pytest==9.0.1
install -d -o root -g root -m 0755 /opt/so2-check/bin
install -o root -g root -m 0755 "$target/deployment/game-client.sh" /opt/so2-check/bin/game-client
install -o root -g root -m 0644 "$target/deployment/so2-check-game-probe.service" "$unit"
install -d -o so2check -g so2check -m 0700 /var/lib/so2-check/source-captures
printf '%s\n' 'standalone TLS/RPC client; live account authentication not configured' > "$target/STATE"
systemd-analyze verify "$unit"
systemctl daemon-reload

printf 'Isolated client tests\n'
cd "$target"
runuser -u so2check -- env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$target" \
  "$venv/bin/python" -m pytest tests -q -p no:cacheprovider
printf 'Real pre-authentication RPC check\n'
systemctl start so2-check-game-probe.service
journalctl --namespace=so2-check -u so2-check-game-probe.service -n 12 --no-pager
printf 'Existing application services\n'
systemctl is-active so2-check-api so2-check-worker so2-check-bot shadow-empire-admin shadow-empire-bot
curl --fail --silent http://127.0.0.1:8171/healthz
printf '\n'
