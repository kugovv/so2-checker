"""Package only the isolated client, its tests and two deployment templates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts" / "so2-game-client-release.tar.gz"


def main():
    entries = []
    for source in sorted((ROOT / "backend/game_client").iterdir()):
        if source.is_file() and source.suffix in {".py", ".json", ".pem", ".txt"}:
            entries.append((source, "game_client/" + source.name))
    for source in sorted((ROOT / "backend/tests").glob("test_game_client*.py")):
        entries.append((source, "tests/" + source.name))
    if not any(target.startswith("tests/") for _, target in entries):
        raise SystemExit("Client tests are missing")
    for name in ["game-client.sh", "so2-check-game-probe.service"]:
        entries.append((ROOT / "infra" / name, "deployment/" + name))
    hashes = {target: hashlib.sha256(source.read_bytes()).hexdigest()
              for source, target in entries}
    manifest = ROOT / "artifacts" / "game-client-release.json"
    manifest.parent.mkdir(exist_ok=True)
    manifest.write_text(json.dumps({"files": hashes}, indent=2) + "\n", encoding="utf-8")
    entries.append((manifest, "deployment/release.json"))
    with tarfile.open(OUTPUT, "w:gz") as archive:
        for source, target in entries:
            info = archive.gettarinfo(str(source), arcname=target)
            info.mode, info.uid, info.gid = 0o644, 0, 0
            info.uname = info.gname = "root"
            with source.open("rb") as stream:
                archive.addfile(info, stream)
    print(json.dumps({"files": len(entries), "archive_bytes": OUTPUT.stat().st_size,
                      "archive_sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
