"""Verified TLS RPC transport with explicit protocol profiles and no password flow.

TLS_RPC follows public AstandyClient PR #4, commit
6b0d97c0ed1d80fdd7b5f81000ecea1496366449 (hello/AES removed). LEGACY_AES
follows PyPI 0.3.0.2. Neither profile being available proves live compatibility.
Only a correlated, nonempty, valid handshake response authenticates a session.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from importlib.resources import files
import secrets
import ssl
import time
from typing import Awaitable, Callable
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .crypto import LegacyCipher
from .errors import (AuthenticationRequired, GameClientError, PlayerIdentityMismatch,
                     ProtocolError, RpcError, RpcTimeout, SessionUnavailable, TransportError)
from .protocol import Method, client_payload, frame, read_frame, server_responses
from .schemas import BOLT, SUPPORT, message, parse
from .session import TicketProvider, TicketSession

GAME_HOST = "server.boltgaming.io"
GAME_PORT = 2223


def validate_public_uid(uid: str) -> str:
    if not isinstance(uid, str) or not uid.isascii() or not uid.isdigit() or not 1 <= len(uid) <= 20:
        raise ValueError("invalid public game UID")
    return uid


class ProtocolProfile(str, Enum):
    TLS_RPC = "tls-rpc"
    LEGACY_AES = "legacy-aes"


@dataclass(frozen=True)
class ResolvedPlayer:
    uid: str
    gpid: str = field(repr=False)
    profile: object = field(repr=False)


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(files(__package__).joinpath("game-ca.pem")))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # Never accept a missing CA, mismatched hostname, or unverified certificate.
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise ProtocolError()
    return context


class GameClient:
    def __init__(self, *, profile: ProtocolProfile = ProtocolProfile.TLS_RPC,
                 named_legacy_hello: bool = False, timeout: float = 10,
                 ping_interval: float = 5, ping_timeout: float = 15,
                 minimum_rpc_interval: float = 0.4,
                 connector: Callable[..., Awaitable] = asyncio.open_connection):
        self.profile = ProtocolProfile(profile)
        if named_legacy_hello and self.profile is not ProtocolProfile.LEGACY_AES:
            raise ValueError("named hello requires legacy-aes profile")
        if timeout <= 0 or ping_interval <= 0 or ping_timeout <= 0 or minimum_rpc_interval < 0:
            raise ValueError("invalid transport limits")
        self.named_legacy_hello = named_legacy_hello
        self.timeout = timeout
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.minimum_rpc_interval = minimum_rpc_interval
        self._connector = connector
        self._reader = None
        self._writer = None
        self._reader_task = None
        self._ping_task = None
        self._connected = False
        self._authenticated = False
        self._hello_validated = False
        self._session: TicketSession | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._write_lock = asyncio.Lock()
        self._rate_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(4)
        self._last_rpc = 0.0
        self._pong = asyncio.Event()
        self._cipher = LegacyCipher() if self.profile is ProtocolProfile.LEGACY_AES else None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def authenticated(self) -> bool:
        return self._connected and self._authenticated

    @property
    def hello_validated(self) -> bool:
        return self._connected and self._hello_validated

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exception):
        await self.close()

    async def connect(self) -> None:
        if self._connected:
            return
        await self.close()
        self._cipher = LegacyCipher() if self.profile is ProtocolProfile.LEGACY_AES else None
        try:
            self._reader, self._writer = await asyncio.wait_for(self._connector(
                host=GAME_HOST, port=GAME_PORT, ssl=tls_context(), server_hostname=GAME_HOST), self.timeout)
            self._connected = True
            self._reader_task = asyncio.create_task(self._read_loop(), name="game-rpc-reader")
            if self.profile is ProtocolProfile.LEGACY_AES:
                await self._legacy_hello()
            self._ping_task = asyncio.create_task(self._ping_loop(), name="game-rpc-heartbeat")
        except (OSError, ConnectionError, asyncio.TimeoutError):
            await self.close()
            raise TransportError() from None
        except BaseException:
            await self.close()
            raise

    def _abort(self, error: GameClientError) -> None:
        self._connected = self._authenticated = self._hello_validated = False
        self._session = None
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        if self._writer is not None:
            self._writer.close()

    async def close(self) -> None:
        self._abort(TransportError())
        current = asyncio.current_task()
        tasks = [task for task in (self._reader_task, self._ping_task) if task is not None and task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._reader_task = self._ping_task = None
        if self._writer is not None:
            try:
                await asyncio.wait_for(self._writer.wait_closed(), 2)
            except (OSError, ConnectionError, asyncio.TimeoutError):
                pass
        self._reader = self._writer = None
        self._cipher = None

    async def _send(self, body: bytes) -> None:
        if not self._connected or self._writer is None:
            raise TransportError()
        try:
            async with self._write_lock:
                self._writer.write(frame(body))
                await asyncio.wait_for(self._writer.drain(), self.timeout)
        except (OSError, ConnectionError, asyncio.TimeoutError):
            self._abort(TransportError())
            raise TransportError() from None

    async def _read_loop(self) -> None:
        try:
            while self._connected:
                body = await read_frame(self._reader)
                if body == b"\x01":
                    self._pong.set()
                    continue
                for response in server_responses(body):
                    future = self._pending.get(response.id)
                    if future is not None and not future.done():
                        future.set_result(response)
        except asyncio.CancelledError:
            raise
        except GameClientError as error:
            self._abort(error)
        except Exception:
            # No response contents or private exception properties in error text.
            self._abort(ProtocolError())

    async def _ping_loop(self) -> None:
        try:
            while self._connected:
                await asyncio.sleep(self.ping_interval)
                if not self._connected:
                    return
                self._pong.clear()
                await self._send(b"\x01")
                await asyncio.wait_for(self._pong.wait(), self.ping_timeout)
        except asyncio.CancelledError:
            raise
        except (GameClientError, asyncio.TimeoutError):
            self._abort(TransportError())

    async def _rpc(self, method: Method, request, *, named: bool = False):
        if not isinstance(method, Method) or request.DESCRIPTOR.full_name != method.value.request:
            raise ProtocolError()
        if not self._connected:
            raise TransportError()
        async with self._slots:
            async with self._rate_lock:
                delay = self.minimum_rpc_interval - (time.monotonic() - self._last_rpc)
                if delay > 0:
                    await asyncio.sleep(delay)
                self._last_rpc = time.monotonic()
            request_id = str(uuid4())
            future = asyncio.get_running_loop().create_future()
            self._pending[request_id] = future
            try:
                body = request.SerializeToString()
                secure = self.profile is ProtocolProfile.LEGACY_AES and method.value.legacy_secure
                if secure:
                    body = self._cipher.encrypt(body)
                await self._send(client_payload(method, request_id, body, named=named))
                try:
                    response = await asyncio.wait_for(future, self.timeout)
                except asyncio.TimeoutError:
                    raise RpcTimeout() from None
                if response.exception.code:
                    if response.exception.code in (401, 2003):  # Unauthorized / published "Session time expired".
                        self._authenticated = False
                        self._session = None
                    raise RpcError(response.exception.code)
                if len(response.data) != 1 or response.data[0].isNull or response.data[0].array:
                    raise ProtocolError()
                body = response.data[0].one
                if secure:
                    body = self._cipher.decrypt(body)
                # A typed empty history list is legal only inside a nonempty RPC
                # BinaryValue. No data array at all is never interpreted as success.
                return parse(method.value.response, body)
            finally:
                self._pending.pop(request_id, None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    # Consume any simultaneous disconnect error on cancellation.
                    future.exception()

    async def _legacy_hello(self) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        public = private_key.public_key().public_numbers()
        iv = secrets.token_bytes(16)
        response = await self._rpc(Method.HELLO, message(SUPPORT + "HelloRequest",
            n=public.n.to_bytes(128, "big"), e=public.e.to_bytes(3, "big"), iv=iv),
            named=self.named_legacy_hello)
        if len(response.key) != 128:
            raise ProtocolError()
        try:
            key = private_key.decrypt(response.key, padding.PKCS1v15())
        except ValueError:
            raise ProtocolError() from None
        self._cipher = LegacyCipher(key, iv)
        self._hello_validated = True

    async def authenticate(self, provider: TicketProvider) -> None:
        async with self._auth_lock:
            if not self._connected:
                raise TransportError()
            session = await provider.get_ticket()
            session.validate()
            if self.authenticated and self._session == session:
                return
            self._authenticated = False
            self._session = None
            response = await self._rpc(Method.HANDSHAKE, message(BOLT + "Handshake", ticket=session.ticket))
            if not response.ListFields():
                raise ProtocolError()
            self._session = session
            self._authenticated = True

    async def probe_authorization_boundary(self) -> dict:
        """One known read-only RPC, without a ticket; no profile values exposed.

        A correlated 401 demonstrates a working RPC envelope and an authorization
        boundary. It does not confirm that UID/history methods remain supported.
        """
        if self._authenticated:
            raise ProtocolError()
        try:
            await self._rpc(Method.SELF, message(BOLT + "GetPlayerRequest"))
        except RpcError as error:
            return {"rpc_response_received": True, "rpc_error_code": error.remote_code,
                    "rpc_boundary_verified": error.remote_code == 401,
                    "game_authenticated": False, "player_data_received": False}
        return {"rpc_response_received": True, "rpc_boundary_verified": False,
                "game_authenticated": False, "unexpected_self_response": True}

    def _require_authenticated(self):
        if not self.authenticated or self._session is None:
            raise AuthenticationRequired()
        try:
            self._session.validate()
        except SessionUnavailable:
            self._authenticated = False
            self._session = None
            raise AuthenticationRequired() from None

    async def resolve_uid(self, uid: str) -> ResolvedPlayer:
        self._require_authenticated()
        validate_public_uid(uid)
        response = await self._rpc(Method.RESOLVE_UID, message(BOLT + "GetPlayerByUidRequest", uid=uid))
        if not response.HasField("player") or response.player.uid != uid or not response.player.gpid:
            raise PlayerIdentityMismatch()
        return ResolvedPlayer(uid, response.player.gpid, response.player)

    async def history_page(self, player: ResolvedPlayer, *, offset: int, length: int = 20):
        self._require_authenticated()
        if not isinstance(player, ResolvedPlayer) or not player.gpid or not 0 <= offset <= 100_000 or not 1 <= length <= 50:
            raise ValueError("invalid history request")
        request = message(BOLT + "matches.GetPlayerMatchesRequest", gpid=player.gpid,
                          offset=message(BOLT + "Offset", offset=offset, length=length))
        response = await self._rpc(Method.HISTORY, request)
        if len(response.matches) > length:
            raise ProtocolError()
        return tuple(response.matches)

    async def get_match(self, match_id: str):
        self._require_authenticated()
        if not isinstance(match_id, str) or not match_id or len(match_id) > 256:
            raise ValueError("invalid match ID")
        response = await self._rpc(Method.MATCH, message(BOLT + "matches.GetMatchRequest", matchId=match_id))
        if not response.HasField("match") or response.match.matchId != match_id:
            raise ProtocolError()
        return response.match
