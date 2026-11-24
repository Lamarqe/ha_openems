"""Component providing support for OpenEMS number entities."""

from dataclasses import dataclass
import logging
from typing import Any

from openems_client.model import ChannelKind
from openems_client.openems import OpenEMSBooleanProperty, OpenEMSComponent

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .entity import OpenEMSChannelEntity
from .helpers_ha import OpenEMSConfigEntry, component_device, handle_write_errors

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS switch entities."""

    def _create_switch_entities(component: OpenEMSComponent) -> None:
        """Create Sensor Entities from channel list."""
        device = component_device(entry.runtime_data.edge_device.entry, component)

        entities: list[OpenEMSSwitchEntity] = []
        channel: OpenEMSBooleanProperty
        channel_list: list[OpenEMSBooleanProperty] = component.channels_of(
            ChannelKind.SWITCH, OpenEMSBooleanProperty
        )
        for channel in channel_list:
            entity_enabled = channel.primary
            entity_description = OpenEMSSwitchDescription(
                key=channel.unique_id,
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=entity_enabled,
                # primary channels have a translated name, others a generated one
                name=UNDEFINED if entity_enabled else channel.display_name,
                translation_key=channel.key,
            )
            entities.append(
                OpenEMSSwitchEntity(
                    channel,
                    entity_description,
                    device,
                )
            )
        async_add_entities(entities)

    component: OpenEMSComponent
    for component in entry.runtime_data.enabled_components:
        _create_switch_entities(component)


@dataclass(frozen=True, kw_only=True)
class OpenEMSSwitchDescription(SwitchEntityDescription):
    """Definition of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSSwitchEntity(OpenEMSChannelEntity[OpenEMSBooleanProperty], SwitchEntity):
    """Number entity class for OpenEMS channels."""

    entity_description: OpenEMSSwitchDescription

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        return self._channel.is_on

    @handle_write_errors
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self._channel.update_value(True)
        self.async_write_ha_state()

    @handle_write_errors
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self._channel.update_value(False)
        self.async_write_ha_state()
