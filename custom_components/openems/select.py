"""Component providing support for OpenEMS select entities."""

from dataclasses import dataclass

from openems_client.model import ChannelKind
from openems_client.openems import OpenEMSComponent, OpenEMSEnumProperty

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import UNDEFINED

from .entity import OpenEMSChannelEntity
from .helpers_ha import OpenEMSConfigEntry, component_device, handle_write_errors

# writes of update groups also send the current values of other properties
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS select entities."""

    def _create_select_entities(component: OpenEMSComponent) -> None:
        """Create select entities from channel list."""
        device = component_device(entry.runtime_data.edge_device.entry, component)

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
                # primary channels have a translated name, others a generated one
                name=UNDEFINED if entity_enabled else channel.display_name,
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

    component: OpenEMSComponent
    for component in entry.runtime_data.enabled_components:
        _create_select_entities(component)


@dataclass(frozen=True, kw_only=True)
class OpenEMSSelectDescription(SelectEntityDescription):
    """Definition of OpenEMS select attributes."""

    has_entity_name = True


class OpenEMSSelectEntity(OpenEMSChannelEntity[OpenEMSEnumProperty], SelectEntity):
    """Select entity class for OpenEMS channels."""

    entity_description: OpenEMSSelectDescription

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        return self._channel.value

    @handle_write_errors
    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self._channel.update_value(option)
        self.async_write_ha_state()
