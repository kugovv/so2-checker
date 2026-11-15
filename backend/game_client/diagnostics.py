"""Bounded field schemas by default, optional owner-only game-response snapshots."""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import tempfile

from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.json_format import MessageToDict

from .errors import ProtocolError

MAX_DIAGNOSTIC_BYTES = 2 * 1024 * 1024


class FieldReport:
    def __init__(self):
        self.fields = {}
        self.messages = Counter()
        self.dynamic_names = {}

    def observe(self, value, path: str, depth: int = 0):
        if depth > 12 or len(self.fields) > 2000:
            raise ProtocolError()
        self.messages[path] += 1
        present = {descriptor.name for descriptor, _ in value.ListFields()}
        for descriptor in value.DESCRIPTOR.fields:
            field_path = path + "." + descriptor.name
            info = self.fields.setdefault(field_path, {
                "protobuf_type": descriptor.type,
                "type_name": descriptor.message_type.full_name if descriptor.message_type else (
                    descriptor.enum_type.full_name if descriptor.enum_type else None),
                "repeated": descriptor.label == FieldDescriptor.LABEL_REPEATED,
                "observed": 0, "present_nondefault": 0,
            })
            info["observed"] += 1
            if descriptor.name not in present:
                continue
            info["present_nondefault"] += 1
            if descriptor.type != FieldDescriptor.TYPE_MESSAGE:
                continue
            child = getattr(value, descriptor.name)
            if descriptor.message_type.GetOptions().map_entry:
                info["map_entries_observed"] = info.get("map_entries_observed", 0) + len(child)
                # Profile attribute keys/values can include private information;
                # do not include them in the default schema report.
                continue
            children = child if info["repeated"] else [child]
            for item in children:
                if descriptor.name in {"properties", "stats", "stat"} and "name" in item.DESCRIPTOR.fields_by_name:
                    name = item.name
                    if name and len(name) <= 128 and all(c.isalnum() or c in "_.:-/" for c in name):
                        bucket = self.dynamic_names.setdefault(field_path, {})
                        if name in bucket or len(bucket) < 128:
                            bucket[name] = bucket.get(name, 0) + 1
                self.observe(item, field_path, depth + 1)

    def summary(self):
        return {"messages_observed": dict(self.messages), "fields": self.fields,
                "dynamic_names_observed": self.dynamic_names,
                "presence_note": "proto3 nonoptional scalar defaults have no wire presence; absent is not measured zero",
                "statistic_mapping_verified": False}


def private_json(path: str | Path, value) -> None:
    destination = Path(path)
    temporary = None
    try:
        if destination.is_symlink():
            raise ProtocolError()
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".game-report-", dir=destination.parent, delete=False) as stream:
            temporary = stream.name
            os.chmod(temporary, 0o600)
            written = 0
            for chunk in json.JSONEncoder(ensure_ascii=False, indent=2).iterencode(value):
                encoded = chunk.encode("utf-8")
                written += len(encoded)
                if written >= MAX_DIAGNOSTIC_BYTES:
                    raise ProtocolError()
                stream.write(encoded)
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    except OSError:
        raise ProtocolError() from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def game_snapshot(player, batch) -> dict:
    # Never accept handshake/auth messages here. Profile attributes are NOT
    # assumed public: this is a private, bounded investigation snapshot only.
    if player.profile.DESCRIPTOR.full_name != "com.axlebolt.bolt.Player":
        raise ProtocolError()
    if any(item.DESCRIPTOR.full_name != "com.axlebolt.bolt.matches.FinishedMatch" for item in batch.matches):
        raise ProtocolError()
    return {"profile": MessageToDict(player.profile, preserving_proto_field_name=True),
            "matches": [MessageToDict(item, preserving_proto_field_name=True) for item in batch.matches],
            "history": batch.summary(), "formula_inputs_verified": False}
