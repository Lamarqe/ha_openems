"""Component providing support for OpenEMS updates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

import jsonrpc_base

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

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install an update."""
        update_task = asyncio.create_task(self._edge.execute_system_update())
        # give the backend some time to start the update before checking progress
        update_start_time = datetime.now()
        await asyncio.sleep(0.5)
        try:
            # if the update did not finish after 15 minutes, something went wrong
            while datetime.now() - update_start_time < timedelta(minutes=15):
                await self.async_update()
                self.async_write_ha_state()
                if not self.in_progress:
                    _LOGGER.info("Update finished succesfully: %s", self.unique_id)
                    return
                await asyncio.sleep(10)
            _LOGGER.info(
                "Update still running after 15 minutes. Stop waiting to finish: %s",
                self.unique_id,
            )
        finally:
            if not update_task.done():
                update_task.cancel()

    async def async_update(self) -> None:
        """Trigger the entity status update, will be called after SCAN_INTERVAL."""
        try:
            state = await self._edge.get_system_update_state()
            status = next(iter(state))
            match status:
                case "updated":
                    self._set_versions(state[status]["version"])
                case "available":
                    self._set_versions(
                        state[status]["currentVersion"], state[status]["latestVersion"]
                    )
                case "running":
                    self._set_progress_percentage(state[status]["percentCompleted"])
                case _:
                    self._set_versions(None)

        except (jsonrpc_base.TransportError, jsonrpc_base.jsonrpc.ProtocolError):
            self._set_versions(None)

    def _set_versions(self, curr_ver: str | None, new_ver: str | None = None) -> None:
        self._attr_available = bool(curr_ver)
        self._attr_installed_version = curr_ver
        self._attr_latest_version = new_ver if new_ver else curr_ver
        # reset potential update in progress indicators
        self._attr_in_progress = self.in_progress and not self._attr_available
        self._attr_update_percentage = None

    def _set_progress_percentage(self, percentage: int) -> None:
        self._attr_available = True
        self._attr_in_progress = True
        self._attr_update_percentage = percentage
