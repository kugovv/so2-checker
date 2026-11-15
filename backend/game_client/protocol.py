"""Bounded framing and the only allowed RPC operations."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum

import lz4.block

from .errors import ProtocolError, TransportError
from .schemas import BASE, BOLT, SUPPORT, message, parse

MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_EXPANDED_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_ITEMS = 1024


@dataclass(frozen=True)
class RpcSpec:
    service: str
    method: str
    code: int
    request: str
    response: str
    legacy_secure: bool = False


class Method(Enum):
    HELLO = RpcSpec("HelloRemoteService", "hello", 1, SUPPORT + "HelloRequest", SUPPORT + "HelloResponse", True)
    HANDSHAKE = RpcSpec("HandshakeRemoteService", "encryptedHandshake", 3, BOLT + "Handshake", BOLT + "HandshakeResponse", True)
    RESOLVE_UID = RpcSpec("PlayerRemoteService", "getPlayerByUid", 0, BOLT + "GetPlayerByUidRequest", BOLT + "GetPlayerByUidResponse")
    HISTORY = RpcSpec("MatchesRemoteService", "getPlayerMatches", 0, BOLT + "matches.GetPlayerMatchesRequest", BOLT + "matches.GetPlayerMatchesResponse")
    MATCH = RpcSpec("MatchesRemoteService", "getMatch", 0, BOLT + "matches.GetMatchRequest", BOLT + "matches.GetMatchResponse")
    SELF = RpcSpec("PlayerRemoteService", "getPlayer2", 23, BOLT + "GetPlayerRequest", BOLT + "GetPlayerResponse")


def client_payload(method: Method, request_id: str, body: bytes, *, named: bool = False) -> bytes:
    if not isinstance(method, Method):
        raise ProtocolError()
    spec = method.value
    code = 0 if named else spec.code
    packet = message(BASE + "ClientMsg", id=request_id, code=code)
    if code == 0:
        packet.cls = spec.service
        packet.func = spec.method
    packet.data.add(one=body)
    return packet.SerializeToString()


def frame(payload: bytes, maximum: int = MAX_FRAME_BYTES) -> bytes:
    if not payload or len(payload) > maximum:
        raise ProtocolError()
    return len(payload).to_bytes(4, "big") + payload


async def read_frame(reader: asyncio.StreamReader, maximum: int = MAX_FRAME_BYTES) -> bytes:
    try:
        size = int.from_bytes(await reader.readexactly(4), "big")
        if not 0 < size <= maximum:
            raise ProtocolError()
        return await reader.readexactly(size)
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        raise TransportError() from None


def server_responses(payload: bytes):
    """Expand one frame; shared budget bounds nested LZ4 and response counts."""
    responses = []
    budget = MAX_EXPANDED_BYTES
    items = 0

    def expand(body, depth):
        nonlocal budget, items
        if depth > 2:
            raise ProtocolError()
        packet = parse(BASE + "ServerMsg", body)
        items += len(packet.responses) + len(packet.events) + len(packet.compressed_instances)
        if not packet.ListFields() or items > MAX_RESPONSE_ITEMS:
            raise ProtocolError()
        for response in packet.responses:
            if not response.id:
                raise ProtocolError()
            responses.append(response)
        for compressed in packet.compressed_instances:
            size = compressed.uncompressed_size
            if not 0 < size <= budget or not compressed.compressed:
                raise ProtocolError()
            budget -= size
            try:
                unpacked = lz4.block.decompress(compressed.compressed, uncompressed_size=size)
            except (ValueError, OverflowError, lz4.block.LZ4BlockError):
                raise ProtocolError() from None
            if len(unpacked) != size:
                raise ProtocolError()
            expand(unpacked, depth + 1)

    expand(payload, 0)
    return responses
