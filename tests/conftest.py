"""Common fixtures for the HA OpenEMS tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest


# pytest-homeassistant-custom-component pre-populates DATA_CUSTOM_COMPONENTS={}
# to block custom integrations by default. This autouse fixture requests the
# plugin's own enable_custom_integrations fixture so every test can find
# custom_components/openems without having to declare it individually.
@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Enable custom integrations for all tests."""


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry and async_unload_entry."""
    with (
        patch(
            "custom_components.openems.async_setup_entry", return_value=True
        ) as mock_setup_entry,
        patch(
            "custom_components.openems.async_unload_entry", return_value=True
        ),
    ):
        yield mock_setup_entry
