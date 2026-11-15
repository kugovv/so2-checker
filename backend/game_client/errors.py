"""Errors deliberately exclude remote exception properties and secret values."""


class GameClientError(Exception):
    code = "game_client_error"

    def __init__(self):
        super().__init__(self.code)


class TransportError(GameClientError):
    code = "transport_unavailable"


class RpcTimeout(GameClientError):
    code = "rpc_timeout"


class ProtocolError(GameClientError):
    code = "invalid_protocol_response"


class RpcError(GameClientError):
    code = "rpc_rejected"

    def __init__(self, remote_code: int):
        self.remote_code = remote_code
        super().__init__()


class AuthenticationRequired(GameClientError):
    code = "game_authentication_required"


class SessionUnavailable(GameClientError):
    code = "game_session_unavailable"


class UnsupportedAuthentication(GameClientError):
    code = "password_login_not_supported"


class PlayerIdentityMismatch(GameClientError):
    code = "player_identity_mismatch"


class PaginationError(GameClientError):
    code = "invalid_history_page"
