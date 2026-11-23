"""Tests for the OpenEMS channel entities."""

from datetime import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.openems.const import DOMAIN
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.time import (
    ATTR_TIME,
    DOMAIN as TIME_DOMAIN,
    SERVICE_SET_VALUE as TIME_SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError


@pytest.mark.parametrize(
    ("domain", "service", "entity_id", "data"),
    [
        pytest.param(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            "number.fems12345_ctrlemergencycapacityreserve0_reserve_soc",
            {ATTR_VALUE: 40},
            id="number",
        ),
        pytest.param(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            "select.fems12345_ctrlevcs0_priority",
            {ATTR_OPTION: "storage"},
            id="select",
        ),
        pytest.param(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            "switch.fems12345_ctrlemergencycapacityreserve0_reserve_soc_enabled",
            {},
            id="switch",
        ),
        pytest.param(
            TIME_DOMAIN,
            TIME_SERVICE_SET_VALUE,
            "time.fems12345_ctrlgridoptimizedcharge0_manual_target_time",
            {ATTR_TIME: time(7, 15)},
            id="time",
        ),
    ],
)
@pytest.mark.usefixtures("init_integration")
async def test_write_error(
    hass: HomeAssistant,
    mock_openems_client: MagicMock,
    domain: str,
    service: str,
    entity_id: str,
    data: dict[str, Any],
) -> None:
    """A value which cannot be written raises a translated error."""
    mock_openems_client.backend.connection.rpc_server.connected = False

    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
        )

    assert exc_info.value.translation_domain == DOMAIN
    assert exc_info.value.translation_key == "write_failed"
    assert exc_info.value.translation_placeholders == {"entity_id": entity_id}
