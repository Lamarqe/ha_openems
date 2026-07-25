"""Shared helpers for config-flow and options-flow tests."""

from unittest.mock import AsyncMock, MagicMock

LOGIN_RESPONSE_SINGLE: dict = {"user": {"hasMultipleEdges": False}}


def make_mock_connection(login_response: dict | None = None) -> MagicMock:
    """Return an AsyncMock connection for config-flow tests."""
    if login_response is None:
        login_response = LOGIN_RESPONSE_SINGLE
    mock_conn = MagicMock()
    mock_conn.connect_to_server = AsyncMock()
    mock_conn.login_to_server = AsyncMock(return_value=login_response)
    mock_conn.stop = AsyncMock()
    return mock_conn
