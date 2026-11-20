"""Common fixtures for the HA OpenEMS tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from yarl import URL


# pytest-homeassistant-custom-component pre-populates DATA_CUSTOM_COMPONENTS={}
# to block custom integrations by default. This autouse fixture requests the
# plugin's own enable_custom_integrations fixture so every test can find
# custom_components/openems without having to declare it individually.
@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Enable custom integrations for all tests."""


EDGE_HOSTNAME = "fems1"


@pytest.fixture(autouse=True)
def mock_edge_hostname() -> Generator[AsyncMock]:
    """Return EDGE_HOSTNAME when the config flow reads the edge hostname."""
    with patch(
        "openems_client.entry_data.OpenEMSConfigReader.get_channel_values_via_websocket",
        return_value={"_host/Hostname": EDGE_HOSTNAME},
    ) as mock_read:
        yield mock_read


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry and async_unload_entry."""
    with (
        patch(
            "custom_components.openems.async_setup_entry", return_value=True
        ) as mock_setup_entry,
        patch("custom_components.openems.async_unload_entry", return_value=True),
    ):
        yield mock_setup_entry


@pytest.fixture
def dummy_backend():
    """Minimal mock OpenEMSBackend for unit tests that construct OpenEMSEdge directly."""
    backend = MagicMock()
    backend.multi_edge = False
    backend.connection.conn_url = URL("ws://localhost:8085/openems-backend-ui")
    return backend
