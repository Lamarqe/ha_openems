"""Component providing support for OpenEMS updates."""

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from openems_client.exceptions import OpenEMSError, OpenEMSTimeoutError
from openems_client.openems import OpenEMSBackend, OpenEMSEdge
from openems_client.system_update import SystemUpdateState, UpdateStatus

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .helpers_ha import OpenEMSConfigEntry

SCAN_INTERVAL = timedelta(hours=12)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS update entity."""
    backend: OpenEMSBackend = entry.runtime_data.backend
    entity_description = OpenEMSUpdateDescription(
        key=update_unique_id(backend.the_edge)
    )

    update_entity = OpenEMSUpdateEntity(
        backend.the_edge, entity_description, entry.runtime_data.edge_device.info
    )
    async_add_entities([update_entity], update_before_add=True)


def update_unique_id(edge: OpenEMSEdge) -> str:
    """Return the unique id of the update entity of the given edge."""
    return edge.hostname + "/" + edge.id + "/" + "update"


@dataclass(frozen=True, kw_only=True)
class OpenEMSUpdateDescription(UpdateEntityDescription):
    """Definition of OpenEMS update attributes."""

    has_entity_name = True
    device_class = UpdateDeviceClass.FIRMWARE
    entity_registry_enabled_default = True
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
        if edge.has_release_notes:
            self._attr_supported_features |= UpdateEntityFeature.RELEASE_NOTES
        self._attr_release_url = edge.release_url

    async def async_release_notes(self) -> str | None:
        """Return the changes from the installed to the latest version."""
        return await self._edge.get_release_notes(self.hass.config.language)

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install the update and show its progress until the edge reports it done."""
        try:
            await self._edge.install_system_update(self._handle_progress)
        except OpenEMSTimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="update_not_finished",
                translation_placeholders={"edge": self._edge.hostname},
            ) from err
        except OpenEMSError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"edge": self._edge.hostname},
            ) from err
        _LOGGER.info("Update finished successfully: %s", self.unique_id)

    @callback
    def _handle_progress(self, state: SystemUpdateState) -> None:
        self._apply_state(state)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Read the update state, called every SCAN_INTERVAL."""
        try:
            state = await self._edge.get_system_update_state()
        except OpenEMSError:
            state = SystemUpdateState(status=UpdateStatus.UNKNOWN)
        self._apply_state(state)

    def _apply_state(self, state: SystemUpdateState) -> None:
        match state.status:
            case UpdateStatus.RUNNING:
                self._attr_available = True
                self._attr_in_progress = True
                self._attr_update_percentage = state.percent_completed
            case UpdateStatus.UNKNOWN:
                # eg while the edge restarts during an update, which stays in progress
                self._attr_available = False
                self._attr_installed_version = None
                self._attr_latest_version = None
                self._attr_update_percentage = None
            case _:
                self._attr_available = True
                self._attr_installed_version = state.installed_version
                self._attr_latest_version = state.latest_version
                self._attr_in_progress = False
                self._attr_update_percentage = None
