"""Component providing support for OpenEMS updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.update import (
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import OpenEMSConfigEntry
from .const import DOMAIN
from .openems import OpenEMSBackend, OpenEMSEdge

SCAN_INTERVAL = timedelta(hours=12)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenEMS update entity."""
    backend: OpenEMSBackend = entry.runtime_data.backend
    # Create the edge device
    edge_device = DeviceInfo(
        name=backend.the_edge.hostname,
        identifiers={(DOMAIN, backend.the_edge.hostname)},
    )
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(**edge_device, config_entry_id=entry.entry_id)

    unique_id = backend.the_edge.hostname + "/" + backend.the_edge.id + "/" + "update"
    entity_description = OpenEMSUpdateDescription(key=unique_id)

    update_entity = OpenEMSUpdateEntity(
        backend.the_edge, entity_description, edge_device
    )
    async_add_entities([update_entity], update_before_add=True)


@dataclass(frozen=True, kw_only=True)
class OpenEMSUpdateDescription(UpdateEntityDescription):
    """Defintion of OpenEMS sensor attributes."""

    has_entity_name = True
    entity_registry_enabled_default = True
    name = "Update"
    should_poll = True


class OpenEMSUpdateEntity(UpdateEntity):
    """Representation of the OpenEMS update entity."""

    def __init__(
        self,
        edge: OpenEMSEdge,
        entity_description: OpenEMSUpdateDescription,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the sensor."""
        self._edge: OpenEMSEdge = edge
        self.entity_description = entity_description
        self._attr_unique_id = entity_description.key
        self._attr_device_info = device_info
        self._attr_supported_features = (
            UpdateEntityFeature.PROGRESS | UpdateEntityFeature.INSTALL
        )

        self._state = None

    @property
    def available(self) -> bool:
        """Return if data point is available."""
        return bool(self._state)

    @property
    def installed_version(self) -> str | None:
        """Version installed and in use."""
        status = next(iter(self._state))
        match status:
            case "updated":
                return self._state[status]["version"]
            case "available":
                return self._state[status]["currentVersion"]
            case _:
                return None

    @property
    def in_progress(self) -> bool | int | None:
        """Update installation progress."""
        status = next(iter(self._state))
        return status == "running"

    @property
    def update_percentage(self) -> bool | int | None:
        """Update installation progress."""
        status = next(iter(self._state))
        return self._state[status]["percentCompleted"] if status == "running" else None

    @property
    def latest_version(self) -> str | None:
        """Latest version available for install."""
        status = next(iter(self._state))
        match status:
            case "updated":
                return self._state[status]["version"]
            case "available":
                return self._state[status]["latestVersion"]
            case _:
                return None

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install an update."""
        await self._edge.execute_system_update()

    async def async_update(self) -> None:
        """Trigger the entity status update, will be called after SCAN_INTERVAL."""
        self._state = await self._edge.get_system_update_state()
