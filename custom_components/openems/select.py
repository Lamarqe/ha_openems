"""Component providing support for OpenEMS select entities."""

from dataclasses import dataclass
import logging

from openems_client.model import PROPERTY_PREFIX, ChannelKind
from openems_client.openems import OpenEMSComponent, OpenEMSEnumProperty

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .helpers_ha import OpenEMSConfigEntry, component_device, handle_write_errors

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS select entities."""

    def _create_select_entities(component: OpenEMSComponent) -> None:
        """Create Sensor Entities from channel list."""
        device = component_device(entry.runtime_data.edge_device.entry, component)
        # create empty device explicitly, in case their are no entities
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMSSelectEntity] = []
        channel: OpenEMSEnumProperty
        channel_list: list[OpenEMSEnumProperty] = component.channels_of(
            ChannelKind.SELECT, OpenEMSEnumProperty
        )
        for channel in channel_list:
            entity_enabled = channel.primary
            entity_description = OpenEMSSelectDescription(
                key=channel.unique_id,
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=entity_enabled,
                options=channel.option_keys,
                # primary channels have a translated name, others
                # use the channel name without "_Property" prefix
                name=UNDEFINED
                if entity_enabled
                else channel.name.removeprefix(PROPERTY_PREFIX),
                translation_key=channel.key,
            )
            entities.append(
                OpenEMSSelectEntity(
                    channel,
                    entity_description,
                    device,
                )
            )
        async_add_entities(entities)

    ############ END MARKER _create_select_entities ##############

    component: OpenEMSComponent
    for component in entry.runtime_data.enabled_components:
        _create_select_entities(component)


@dataclass(frozen=True, kw_only=True)
class OpenEMSSelectDescription(SelectEntityDescription):
    """Definition of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSSelectEntity(SelectEntity):
    """Select entity class for OpenEMS channels."""

    entity_description: OpenEMSSelectDescription

    def __init__(
        self,
        channel: OpenEMSEnumProperty,
        entity_description,
        device_info: dr.DeviceInfo,
    ) -> None:
        """Initialize OpenEMS switch entity."""
        self._channel: OpenEMSEnumProperty = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id
        self._attr_device_info = device_info
        self._attr_should_poll = False

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self._channel.value

    @handle_write_errors
    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self._channel.update_value(option)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        self.async_on_remove(
            self._channel.add_listener(self.async_schedule_update_ha_state)
        )
        await super().async_added_to_hass()
