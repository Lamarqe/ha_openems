"""Component providing support for OpenEMS binary sensors."""

from dataclasses import dataclass
import logging

from openems_client.model import ChannelKind
from openems_client.openems import OpenEMSBackend, OpenEMSChannel, OpenEMSComponent

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .const import DOMAIN
from .entity import OpenEMSChannelEntity
from .helpers_ha import OpenEMSConfigEntry, component_device

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
        channel_list: list[OpenEMSChannel] = component.channels_of(
            ChannelKind.BINARY_SENSOR, OpenEMSChannel
        )

        for channel in channel_list:
            entity_description = OpenEMSBinarySensorDescription(
                key=channel.unique_id,
                entity_registry_enabled_default=channel.primary,
                # primary channels have a translated name
                name=UNDEFINED if channel.primary else channel.name,
                # state channels with a level signal a problem of the component
                device_class=BinarySensorDeviceClass.PROBLEM
                if channel.severity
                else None,
                translation_key=channel.key,
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
    for component in entry.runtime_data.enabled_components:
        _create_sensor_entities(component)


@dataclass(frozen=True, kw_only=True)
class OpenEMSBinarySensorDescription(BinarySensorEntityDescription):
    """Definition of OpenEMS binary sensor attributes."""

    has_entity_name = True


class OpenEMSBinarySensorEntity(
    OpenEMSChannelEntity[OpenEMSChannel], BinarySensorEntity
):
    """Representation of a binary sensor."""

    entity_description: OpenEMSBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        """Return the binary state of the sensor."""
        return self._channel.is_on
