import asyncio
import ssl

import lz4.block
import pytest

from game_client.client import GameClient, ProtocolProfile, tls_context
from game_client.crypto import LegacyCipher
from game_client.errors import (AuthenticationRequired, PlayerIdentityMismatch,
                                ProtocolError, RpcError, RpcTimeout, TransportError)
from game_client.protocol import (MAX_EXPANDED_BYTES, MAX_FRAME_BYTES, Method,
                                  client_payload, frame, read_frame, server_responses)
from game_client.schemas import BASE, BOLT, SUPPORT, message, parse
from game_client.session import TicketSession


def run(coroutine):
    return asyncio.run(coroutine)


def response(request_id, body=None, code=0, secret=None):
    packet = message(BASE + "ServerMsg")
    item = packet.responses.add(id=request_id)
    if code:
        item.exception.code = code
    if secret:
        item.exception.properties["private"] = secret
    if body is not None:
        item.data.add(one=body)
    return packet.SerializeToString()


class StubWriter:
    def __init__(self, reader, handler):
        self.reader, self.handler = reader, handler
        self.requests = []
        self.closed = False

    def write(self, payload):
        assert int.from_bytes(payload[:4], "big") == len(payload) - 4
        body = payload[4:]
        if body == b"\x01":
            self.reader.feed_data(frame(body))
            return
        request = parse(BASE + "ClientMsg", body)
        self.requests.append(request)
        reply = self.handler(request)
        if reply is not None:
            self.reader.feed_data(frame(reply))

    async def drain(self):
        pass

    def close(self):
        if not self.closed:
            self.closed = True
            self.reader.feed_eof()

    async def wait_closed(self):
        pass


class StubTransport:
    def __init__(self, handler):
        self.reader = asyncio.StreamReader()
        self.writer = StubWriter(self.reader, handler)
        self.kwargs = None

    async def connect(self, **kwargs):
        self.kwargs = kwargs
        return self.reader, self.writer

    def client(self, **kwargs):
        return GameClient(connector=self.connect, timeout=0.08, minimum_rpc_interval=0,
                          ping_interval=60, **kwargs)


class MemoryProvider:
    async def get_ticket(self):
        return TicketSession("unit-test-private-ticket")


def auth_success(request):
    assert request.code == 3
    assert parse(BOLT + "Handshake", request.data[0].one).ticket == "unit-test-private-ticket"
    return response(request.id, message(BOLT + "HandshakeResponse", timestamp=123456).SerializeToString())


def test_tls_is_always_verified_with_hostname_and_bundled_ca():
    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.get_ca_certs()


def test_fragmented_frame_and_oversized_frame_are_distinct():
    async def check():
        reader = asyncio.StreamReader()
        pending = asyncio.create_task(read_frame(reader))
        wire = frame(b"abcdef")
        for byte in wire:
            reader.feed_data(bytes([byte]))
            await asyncio.sleep(0)
        assert await pending == b"abcdef"
        reader.feed_data((MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
        with pytest.raises(ProtocolError):
            await read_frame(reader)
        reader2 = asyncio.StreamReader()
        reader2.feed_data(b"\x00\x00\x00\x05ab")
        reader2.feed_eof()
        with pytest.raises(TransportError):
            await read_frame(reader2)
    run(check())


def test_compressed_response_budget_and_nested_envelopes():
    original = response("req", b"ok")
    packet = message(BASE + "ServerMsg")
    packet.compressed_instances.add(uncompressed_size=len(original),
                                    compressed=lz4.block.compress(original, store_size=False))
    assert server_responses(packet.SerializeToString())[0].id == "req"
    packet.compressed_instances[0].uncompressed_size = MAX_EXPANDED_BYTES + 1
    with pytest.raises(ProtocolError):
        server_responses(packet.SerializeToString())
    with pytest.raises(ProtocolError):
        server_responses(b"")
    packet.compressed_instances[0].uncompressed_size = len(original)
    packet.compressed_instances[0].compressed = b"not valid lz4"
    with pytest.raises(ProtocolError):
        server_responses(packet.SerializeToString())


def test_preflight_401_is_explicitly_unauthenticated_and_contains_no_secret():
    async def check():
        transport = StubTransport(lambda req: response(req.id, code=401, secret="do-not-print"))
        async with transport.client() as client:
            status = await client.probe_authorization_boundary()
            assert status["rpc_boundary_verified"] is True
            assert status["game_authenticated"] is False
            assert not client.authenticated
            assert len(transport.writer.requests) == 1
            assert transport.writer.requests[0].code == 23
            assert not transport.writer.requests[0].data[0].one
            assert "do-not-print" not in str(status)
        assert transport.writer.closed
        assert transport.kwargs["server_hostname"] == "server.boltgaming.io"
    run(check())


def test_timeout_and_empty_handshake_cannot_authenticate():
    async def check():
        for handler, error in [(lambda req: None, RpcTimeout),
                               (lambda req: response(req.id), ProtocolError),
                               (lambda req: response(req.id, b""), ProtocolError)]:
            transport = StubTransport(handler)
            async with transport.client() as client:
                with pytest.raises(error):
                    await client.authenticate(MemoryProvider())
                assert not client.authenticated
                assert not client._pending
    run(check())


def test_no_read_calls_before_authentication_and_uid_resolution_uses_actual_internal_id():
    async def check():
        def handler(req):
            if req.code == 3:
                return auth_success(req)
            if req.func == "getPlayerByUid":
                assert parse(BOLT + "GetPlayerByUidRequest", req.data[0].one).uid == "202777998"
                return response(req.id, message(BOLT + "GetPlayerByUidResponse", player={
                    "gpid": "internal-opaque-player-key", "uid": "202777998", "name": "Игрок"
                }).SerializeToString())
            if req.func == "getPlayerMatches":
                request = parse(BOLT + "matches.GetPlayerMatchesRequest", req.data[0].one)
                assert request.gpid == "internal-opaque-player-key"
                assert request.offset.offset == 20
                assert request.offset.length == 20
                return response(req.id, b"")  # Explicit typed empty page.
            raise AssertionError("unexpected RPC")
        transport = StubTransport(handler)
        async with transport.client() as client:
            with pytest.raises(AuthenticationRequired):
                await client.resolve_uid("202777998")
            assert not transport.writer.requests
            await client.authenticate(MemoryProvider())
            assert client.authenticated
            player = await client.resolve_uid("202777998")
            assert player.gpid != player.uid
            assert await client.history_page(player, offset=20) == ()
    run(check())


@pytest.mark.parametrize("expired_code", [401, 2003])
def test_mismatched_uid_is_rejected_and_expired_session_is_invalidated(expired_code):
    async def check():
        rejects = False
        def handler(req):
            if req.code == 3:
                return auth_success(req)
            if rejects:
                return response(req.id, code=expired_code, secret="server-secret")
            return response(req.id, message(BOLT + "GetPlayerByUidResponse",
                                           player={"gpid": "internal", "uid": "999"}).SerializeToString())
        transport = StubTransport(handler)
        async with transport.client() as client:
            await client.authenticate(MemoryProvider())
            with pytest.raises(PlayerIdentityMismatch):
                await client.resolve_uid("202777998")
            rejects = True
            with pytest.raises(RpcError) as caught:
                await client.resolve_uid("202777998")
            assert caught.value.remote_code == expired_code
            assert "server-secret" not in repr(caught.value)
            assert not client.authenticated and client._session is None
            with pytest.raises(AuthenticationRequired):
                await client.resolve_uid("202777998")
    run(check())


def test_rpc_cancellation_removes_pending_and_late_reply_is_ignored():
    async def check():
        transport = StubTransport(lambda req: None)
        async with transport.client() as client:
            pending = asyncio.create_task(client.probe_authorization_boundary())
            await asyncio.sleep(0.005)
            request = transport.writer.requests[0]
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert not client._pending
            transport.reader.feed_data(frame(response(request.id, code=401)))
            await asyncio.sleep(0.005)
            assert client.connected
        assert not client._reader_task and not client._ping_task
    run(check())


def test_close_fails_inflight_requests_and_cleans_background_tasks():
    async def check():
        transport = StubTransport(lambda req: None)
        client = transport.client()
        await client.connect()
        pending = asyncio.create_task(client.probe_authorization_boundary())
        await asyncio.sleep(0.005)
        await client.close()
        with pytest.raises(TransportError):
            await pending
        assert not client._pending
        assert not client.authenticated and not client.connected
        assert not client._reader_task and not client._ping_task
    run(check())


def test_rpc_responses_are_correlated_out_of_order_in_one_frame():
    async def check():
        requests = []
        def handler(req):
            requests.append(req)
            if len(requests) == 1:
                return None
            envelope = message(BASE + "ServerMsg")
            envelope.responses.add(id=requests[1].id).exception.code = 403
            envelope.responses.add(id=requests[0].id).exception.code = 401
            return envelope.SerializeToString()
        transport = StubTransport(handler)
        async with transport.client() as client:
            first, second = await asyncio.gather(client.probe_authorization_boundary(),
                                                 client.probe_authorization_boundary())
            assert first["rpc_error_code"] == 401
            assert second["rpc_error_code"] == 403
            assert not client._pending
    run(check())


def test_legacy_hello_is_real_encryption_and_never_implicitly_selected():
    async def check():
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        initial = LegacyCipher()
        session_cipher = None
        def handler(req):
            nonlocal session_cipher
            assert req.code == 0 and req.cls == "HelloRemoteService" and req.func == "hello"
            hello = parse(SUPPORT + "HelloRequest", initial.decrypt(req.data[0].one))
            public = rsa.RSAPublicNumbers(int.from_bytes(hello.e, "big"), int.from_bytes(hello.n, "big")).public_key()
            key = b"0123456789abcdef"
            session_cipher = LegacyCipher(key, hello.iv)
            reply = message(SUPPORT + "HelloResponse", key=public.encrypt(key, padding.PKCS1v15()))
            return response(req.id, initial.encrypt(reply.SerializeToString()))
        transport = StubTransport(handler)
        async with transport.client(profile=ProtocolProfile.LEGACY_AES, named_legacy_hello=True) as client:
            assert client.hello_validated and not client.authenticated
            assert client._cipher.decrypt(session_cipher.encrypt(b"test")) == b"test"
        default = StubTransport(lambda req: None)
        async with default.client() as client:
            assert not default.writer.requests
            assert not client.hello_validated
    run(check())


def test_rpc_allowlist_rejects_unknown_methods_and_wrong_request_schema():
    with pytest.raises(ProtocolError):
        client_payload("deleteAccount", "id", b"")
    async def check():
        transport = StubTransport(lambda req: None)
        async with transport.client() as client:
            with pytest.raises(ProtocolError):
                await client._rpc(Method.SELF, message(BOLT + "Handshake", ticket="private"))
            assert not transport.writer.requests
    run(check())
