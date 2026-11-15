"""Load public protobuf definitions without importing the old Astandy client.

The descriptors come from a pinned source, not a claim of compatibility with
the live server. Typed response validation remains mandatory at every call.
"""
from __future__ import annotations

import base64
import hashlib
from importlib.resources import files
import json

from google.protobuf import descriptor_pool, message_factory
from google.protobuf.message import DecodeError

from .errors import ProtocolError


def _load_pool():
    manifest = json.loads(files(__package__).joinpath("descriptors.json").read_text())
    pool = descriptor_pool.DescriptorPool()
    for entry in manifest["files"].values():
        data = base64.b64decode(entry["descriptor_base64"], validate=True)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ProtocolError()
        pool.AddSerializedFile(data)
    return pool


POOL = _load_pool()


def message(full_name: str, **values):
    return message_factory.GetMessageClass(POOL.FindMessageTypeByName(full_name))(**values)


def parse(full_name: str, payload: bytes):
    try:
        return message(full_name).FromString(payload)
    except (DecodeError, ValueError, TypeError):
        raise ProtocolError() from None


BASE = "Astandy.Base."
SUPPORT = "standoff.rpc.support."
BOLT = "com.axlebolt.bolt."
