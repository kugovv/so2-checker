"""One-time extraction of public protobuf descriptors; does not import Astandy.

Source: the astAndy 0.3.0.2 PyPI sdist checked into research/. The resulting
manifest contains schemas only, never live replies, credentials or sessions.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
from pathlib import Path
import sys


def extract(source_root: Path, destination: Path) -> None:
    from google.protobuf.descriptor_pb2 import FileDescriptorProto

    available = {}
    for source in source_root.rglob("*_pb2.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "AddSerializedFile" and node.args):
                continue
            data = ast.literal_eval(node.args[0])
            descriptor = FileDescriptorProto.FromString(data)
            available[descriptor.name] = (descriptor, data)
    needed = ["Astandy/schemes/messages.proto", "hello_message.proto", "auth_message.proto",
              "player_message.proto", "matches_message.proto"]
    selected = {}

    def include(name):
        if name in selected:
            return
        descriptor, data = available[name]
        for dependency in descriptor.dependency:
            include(dependency)
        selected[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "descriptor_base64": base64.b64encode(data).decode("ascii"),
        }

    for name in needed:
        include(name)
    destination.write_text(json.dumps({
        "source": "astAndy 0.3.0.2 (PyPI sdist), public protocol definitions",
        "source_url": "https://pypi.org/project/astandy/0.3.0.2/",
        "files": selected,
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    extract(Path(sys.argv[1]), Path(sys.argv[2]))
