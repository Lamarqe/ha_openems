"""Component providing support for OpenEMS binary sensors."""

from dataclasses import dataclass
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    EntityPlatform,
    async_get_current_platform,
)

from . import OpenEMSConfigEntry
from .const import ATTR_VALUE, DOMAIN
from .helpers import component_device, translation_key
from .openems import CONFIG, OpenEMSBackend, OpenEMSChannel, OpenEMSComponent

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenEMS binary sensor entities."""

    def _create_sensor_entities(component: OpenEMSComponent) -> None:
        """Create binary sensor entities from channel list."""
        device = component_device(component)
        # create empty device explicitly, in case their are no entities
        device_registry = dr.async_get(hass)

        # ==================== TODO REMOVE_LATER_START ============================
        # remove duplicate (non-binary) sensors if they exist
        entity_registry = er.async_get(hass)
        er_device = device_registry.async_get_device(device.get("identifiers"))
        ha_entities = (
            er.async_entries_for_device(entity_registry, er_device.id, True)
            if er_device
            else []
        )
        # ==================== TODO REMOVE_LATER_END =============================

        device_registry.async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMSBinarySensorEntity] = []
        channel: OpenEMSChannel
        channel_list: list[OpenEMSChannel] = component.boolean_sensors

        for channel in channel_list:
            # ==================== TODO REMOVE_LATER_START ============================
            # remove duplicate (non-binary) sensors if they exist
            wrong_analog_sensors = [
                e
                for e in ha_entities
                if e.domain == Platform.SENSOR and e.unique_id == channel.unique_id()
            ]
            if wrong_analog_sensors:
                entity_registry.async_remove(wrong_analog_sensors[0].entity_id)
            # ==================== TODO REMOVE_LATER_END =============================

            # device_class = BinarySensorDeviceClass.WE_DONT_KNOW
            enable_by_default = CONFIG.is_channel_enabled(component.name, channel.name)
            entity_description = OpenEMSBinarySensorDescription(
                key=channel.unique_id(),
                entity_registry_enabled_default=enable_by_default,
                name=channel.name,
                translation_key=translation_key(channel),
            )
            entities.append(
                OpenEMSBinarySensorEntity(
                    channel,
                    entity_description,
                    device,
                )
            )
        async_add_entities(entities)

    ############ END MARKER _create_sensor_entities ##############

    backend: OpenEMSBackend = entry.runtime_data.backend
    device_registry = dr.async_get(hass)
    # Create the edge device
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        name=backend.the_edge.hostname,
        identifiers={(DOMAIN, backend.the_edge.hostname)},
    )
    component: OpenEMSComponent
    for component in backend.the_edge.components.values():
        if component.create_entities:
            _create_sensor_entities(component)

    # prepare service call
    platform: EntityPlatform = async_get_current_platform()
    platform.async_register_entity_service(
        name="update_value",
        schema={vol.Required(ATTR_VALUE): vol.Coerce(bool)},
        func="update_value",
    )

    # prepare callback for creating in new entities during options config flow
    entry.runtime_data.add_component_callbacks[Platform.SENSOR.value] = (
        _create_sensor_entities
    )


@dataclass(frozen=True, kw_only=True)
class OpenEMSBinarySensorDescription(BinarySensorEntityDescription):
    """Defintion of OpenEMS binary sensor attributes."""

    has_entity_name = True


class OpenEMSBinarySensorEntity(BinarySensorEntity):
    """Representation of a binary sensor."""

    entity_description: OpenEMSBinarySensorDescription

    def __init__(
        self,
        channel: OpenEMSChannel,
        entity_description,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the binary sensor."""
        self._channel: OpenEMSChannel = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_extra_state_attributes = channel.orig_json

    @property
    def is_on(self) -> int | None:
        """Return the binary state of the sensor."""
        if isinstance(self._channel.native_value, int):
            return self._channel.native_value

        return None

    async def update_value(self, **kwargs: Any) -> None:
        """Service callback to change value via REST call."""
        val: bool = bool(kwargs[ATTR_VALUE])

        await self._channel.update_value(val)

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        self._channel.register_callback(
            self.async_schedule_update_ha_state,
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Entity removed."""
        self._channel.unregister_callback()
        await super().async_will_remove_from_hass()
