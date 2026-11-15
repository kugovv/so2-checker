#!/usr/bin/env python3
"""Read-only Standoff 2 probe. Never prints handshake, players, or raw responses.

Run in a dedicated venv with astandy==0.3.0.2; see docs/source-capabilities.md.
An existing game handshake is required. Telegram bot tokens cannot authorize it.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile


class ProbeError(Exception):
    pass


def safe_key(value: str) -> str:
    return re.sub(r"[^\w.\-:/]", "_", value, flags=re.UNICODE)[:128]


class FieldReport:
    """Report schema and non-default wire presence, without field values."""

    def __init__(self):
        self.messages: Counter = Counter()
        self.fields: dict = {}
        self.dynamic: dict = {}

    def observe(self, message, path: str):
        from google.protobuf.descriptor import FieldDescriptor
        self.messages[path] += 1
        present = {field.name: value for field, value in message.ListFields()}
        for field in message.DESCRIPTOR.fields:
            field_path = f"{path}.{field.name}"
            info = self.fields.setdefault(field_path, {
                "protobuf_type": field.type,
                "type_name": field.message_type.full_name if field.message_type else (
                    field.enum_type.full_name if field.enum_type else None),
                "repeated": field.label == FieldDescriptor.LABEL_REPEATED,
                "messages_observed": 0,
                "present_nondefault": 0,
            })
            info["messages_observed"] += 1
            if field.name not in present:
                continue
            info["present_nondefault"] += 1
            if field.type != FieldDescriptor.TYPE_MESSAGE:
                continue
            values = present[field.name] if info["repeated"] else [present[field.name]]
            if field.message_type.GetOptions().map_entry:
                # Dynamic profile attribute names only; never their values.
                info["map_entries_observed"] = info.get("map_entries_observed", 0) + len(values)
                for key, item in list(values.items())[:128]:
                    self.dynamic.setdefault(field_path, {}).setdefault(safe_key(str(key)), {
                        "container": "map_attribute", "observations": 0
                    })["observations"] += 1
                continue
            for child in values:
                if field.name in {"properties", "stat", "stats"} and "name" in child.DESCRIPTOR.fields_by_name:
                    key = safe_key(child.name)
                    if key:
                        bucket = self.dynamic.setdefault(field_path, {})
                        if key not in bucket and len(bucket) >= 128:
                            continue
                        detail = bucket.setdefault(key, {"observations": 0, "types": {}})
                        detail["observations"] += 1
                        type_field = child.DESCRIPTOR.fields_by_name.get("type")
                        if type_field is not None and type_field.enum_type is not None:
                            enum = type_field.enum_type.values_by_number.get(child.type)
                            kind = enum.name if enum else f"UNKNOWN_{child.type}"
                        else:
                            kind = "unknown"
                        detail["types"][kind] = detail["types"].get(kind, 0) + 1
                        if "value" in child.DESCRIPTOR.oneofs_by_name:
                            value_kind = child.WhichOneof("value") or "absent"
                            detail.setdefault("value_presence", {})[value_kind] = detail.setdefault("value_presence", {}).get(value_kind, 0) + 1
                self.observe(child, field_path)

    def as_dict(self):
        return {"message_instances": dict(self.messages), "fields": self.fields,
                "dynamic_property_names_and_types": self.dynamic,
                "presence_caveat": "Proto3 scalar defaults (0, false, empty string) may be omitted on the wire. Missing scalar wire presence is not proof of unknown versus genuine zero. Dynamic Property.value oneof presence can be distinguished."}


async def probe(handshake: str, uid: str, timeout: float):
    from Astandy import StandClient
    from Astandy.generated.protos import common_message_pb2, matches_message_pb2, player_message_pb2

    client = StandClient(handshake, reconnect_enable=False, max_retry_count=1)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "installed_package_version": version("astandy"),
              "authenticated": False, "uid_resolved": False, "pages": [],
              "mode_filter": "none: actual dynamic mode property is not verified",
              "history_scope": "unfiltered source pages; not last 20 eligible matches of a particular mode"}
    fields = FieldReport()
    try:
        await asyncio.wait_for(client.start(), timeout=timeout)
        report["authenticated"] = True
        # The library can return an empty Response after its internal timeout.
        # Read calls require an actual envelope payload; empty proto is not success.
        rpc_call = client.rpc_call

        async def checked_call(target, timeout=15):
            response = await rpc_call(target, timeout=timeout)
            if not response.data and not response.exception.code:
                raise ProbeError("empty_response_or_rpc_timeout")
            return response

        client.rpc_call = checked_call
        profile = await asyncio.wait_for(client.raw.PlayerRemoteService.getPlayerByUid(
            client, player_message_pb2.GetPlayerByUidRequest(uid=uid), timeout=15), timeout=timeout)
        if not profile.HasField("player") or not profile.player.gpid:
            raise ProbeError("uid_not_resolved")
        if profile.player.uid != uid:
            raise ProbeError("uid_resolution_mismatch")
        report["uid_resolved"] = True
        fields.observe(profile.player, "Player")
        seen_ids = set()
        seen_windows = set()
        for offset in (0, 20, 40):
            request = matches_message_pb2.GetPlayerMatchesRequest(
                gpid=profile.player.gpid,
                offset=common_message_pb2.Offset(offset=offset, length=20))
            response = await asyncio.wait_for(client.raw.MatchesRemoteService.getPlayerMatches(
                client, request, timeout=15), timeout=timeout)
            ids = {match.matchId for match in response.matches if match.matchId}
            window = tuple(match.matchId for match in response.matches)
            states = Counter()
            for match in response.matches:
                fields.observe(match, "FinishedMatch")
                state = match.DESCRIPTOR.fields_by_name["state"].enum_type.values_by_number.get(match.state)
                states[state.name if state else "UNKNOWN"] += 1
            report["pages"].append({"offset": offset, "requested_length": 20,
                                    "returned_count": len(response.matches),
                                    "unique_id_count": len(ids),
                                    "new_ids": len(ids - seen_ids),
                                    "overlap_previous_pages": len(ids & seen_ids),
                                    "repeated_window": bool(window and window in seen_windows),
                                    "states": dict(states)})
            seen_ids.update(ids)
            seen_windows.add(window)
            await asyncio.sleep(1)
        report["unique_matches_observed"] = len(seen_ids)
        report["history_beyond_20_observed"] = any(p["offset"] >= 20 and p["new_ids"] > 0 for p in report["pages"])
        report["history_limit_caveat"] = "Three pages establish only this account's observed response at this time. Empty pages do not prove a universal 20-match cap; 60 matches do not prove an unlimited archive."
        report.update(fields.as_dict())
        return report
    finally:
        try:
            await asyncio.wait_for(client.stop(), timeout=3)
        except BaseException:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handshake-file", type=Path, help="Private UTF-8 file containing game handshake (never Telegram token)")
    parser.add_argument("--uid", help="Public in-game UID; resolved to internal gpid by getPlayerByUid")
    parser.add_argument("--report-out", type=Path, default=Path("source-probe-report.json"))
    parser.add_argument("--timeout", type=float, default=40)
    args = parser.parse_args()
    if not args.handshake_file or not args.handshake_file.is_file():
        print("BLOCKED: provide --handshake-file with an authorized current Standoff 2 game handshake. SSH credentials and a Telegram bot token cannot authorize the game API.")
        return 2
    if not args.uid or not re.fullmatch(r"[0-9]{1,20}", args.uid):
        print("BLOCKED: provide --uid with an existing public numeric Standoff 2 ID.")
        return 2
    if os.name != "nt" and args.handshake_file.stat().st_mode & 0o077:
        print("BLOCKED: handshake file permissions must be 0600 or stricter.")
        return 2
    handshake = args.handshake_file.read_text(encoding="utf-8").strip()
    if not handshake or len(handshake) > 65536:
        print("BLOCKED: handshake file is empty or invalid.")
        return 2
    output = args.report_out.resolve()
    logging.disable(logging.CRITICAL)
    original_cwd = Path.cwd()
    # Astandy creates files in cwd on import; isolate and remove those logs.
    try:
        with tempfile.TemporaryDirectory(prefix="so2-source-probe-") as temp:
            try:
                os.chdir(temp)
                result = asyncio.run(probe(handshake, args.uid, max(5, min(args.timeout, 60))))
            finally:
                logging.shutdown()
                os.chdir(original_cwd)
        output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(output, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        os.chmod(output, 0o600)
        print(f"OK: authorized reads completed; compact schema/presence report saved to {output}. No raw responses saved.")
        return 0
    except ModuleNotFoundError:
        print("BLOCKED: install astandy==0.3.0.2 and its dependencies in a dedicated Python >=3.10 virtual environment.")
        return 2
    except Exception as exc:
        # Deliberately do not print exception text: upstream errors can contain data.
        print(f"FAILED: source probe did not complete ({type(exc).__name__}). No real-data compatibility claim can be made.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
