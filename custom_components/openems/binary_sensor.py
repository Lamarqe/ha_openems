"""Component providing support for OpenEMS binary sensors."""

from dataclasses import dataclass
import logging
from typing import Any

from openems_client.openems import OpenEMSBackend, OpenEMSChannel, OpenEMSComponent

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .const import ATTR_TIMEOUT, ATTR_UPDATE_CYCLE, ATTR_VALUE, DOMAIN
from .helpers_ha import (
    OpenEMSConfigEntry,
    component_device,
    is_channel_enabled,
    translation_key,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenEMS binary sensor entities."""

    def _create_sensor_entities(component: OpenEMSComponent) -> None:
        """Create binary sensor entities from channel list."""
        device = component_device(entry.runtime_data.edge_device.entry, component)
        # create empty device explicitly, in case their are no entities
        device_registry = dr.async_get(hass)

        device_registry.async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMSBinarySensorEntity] = []
        channel: OpenEMSChannel
        channel_list: list[OpenEMSChannel] = component.boolean_sensors

        for channel in channel_list:
            # device_class = BinarySensorDeviceClass.WE_DONT_KNOW
            enable_by_default = is_channel_enabled(component.name, channel.name)
            entity_description = OpenEMSBinarySensorDescription(
                key=channel.unique_id(),
                entity_registry_enabled_default=enable_by_default,
                # channels enabled by default have a translated name
                name=UNDEFINED if enable_by_default else channel.name,
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


@dataclass(frozen=True, kw_only=True)
class OpenEMSBinarySensorDescription(BinarySensorEntityDescription):
    """Definition of OpenEMS binary sensor attributes."""

    has_entity_name = True


class OpenEMSBinarySensorEntity(BinarySensorEntity):
    """Representation of a binary sensor."""

    entity_description: OpenEMSBinarySensorDescription

    def __init__(
        self,
        channel: OpenEMSChannel,
        entity_description,
        device_info: dr.DeviceInfo,
    ) -> None:
        """Initialize the binary sensor."""
        self._channel: OpenEMSChannel = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False

    @property
    def is_on(self) -> int | None:
        """Return the binary state of the sensor."""
        if isinstance(self._channel.native_value, int):
            return self._channel.native_value

        return None

    async def update_value(self, **kwargs: Any) -> None:
        """Service callback to change value via REST call."""
        await self._channel.update_value(
            float(kwargs[ATTR_VALUE]),
            kwargs.get(ATTR_UPDATE_CYCLE),  # pyright: ignore[reportArgumentType]
            kwargs.get(ATTR_TIMEOUT),  # pyright: ignore[reportArgumentType]
        )

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
