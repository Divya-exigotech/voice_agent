import aiohttp
import pytest
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError

from app.services.foundry_session import describe_connection_failure


def test_auth_failure_points_to_credentials() -> None:
    message = describe_connection_failure(ClientAuthenticationError("secret details here"))
    assert "AZURE_CLIENT_ID" in message
    assert "secret details" not in message


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "not accepted"), (403, "no permission"), (404, "not found"), (500, "rejected")],
)
def test_http_status_is_explained(status: int, expected: str) -> None:
    error = HttpResponseError(message="body that must not leak")
    error.status_code = status
    message = describe_connection_failure(error)
    assert f"HTTP {status}" in message
    assert expected in message
    assert "must not leak" not in message


def test_handshake_status_is_explained() -> None:
    error = aiohttp.WSServerHandshakeError(None, (), status=403)  # type: ignore[arg-type]
    assert "HTTP 403" in describe_connection_failure(error)


def test_network_error() -> None:
    assert "Could not reach Azure" in describe_connection_failure(ConnectionError("x"))


def test_unknown_error_is_generic() -> None:
    assert describe_connection_failure(ValueError("x")) == "Could not connect to the voice agent"
