"""Component providing support for OpenEMS select entities."""

from dataclasses import dataclass
import logging

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .__init__ import OpenEMSConfigEntry
from .helpers import component_device, translation_key
from .openems import CONFIG, OpenEMSBackend, OpenEMSComponent, OpenEMSEnumProperty

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpenEMSConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up OpenEMS select entities."""

    def _create_select_entities(component: OpenEMSComponent) -> None:
        """Create Sensor Entities from channel list."""
        device = component_device(component)
        # create empty device explicitly, in case their are no entities
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(**device, config_entry_id=entry.entry_id)

        entities: list[OpenEMSSelectEntity] = []
        channel: OpenEMSEnumProperty
        channel_list: list[OpenEMSEnumProperty] = component.enum_properties
        for channel in channel_list:
            entity_enabled = CONFIG.is_channel_enabled(component.name, channel.name)
            entity_description = OpenEMSSelectDescription(
                key=channel.unique_id(),
                entity_category=EntityCategory.CONFIG,
                entity_registry_enabled_default=entity_enabled,
                options=channel.options,
                # remove "_Property" prefix
                name=channel.name[9:],
                translation_key=translation_key(channel),
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

    backend: OpenEMSBackend = entry.runtime_data.backend
    component: OpenEMSComponent
    for component in backend.the_edge.components.values():
        if component.create_entities:
            _create_select_entities(component)

    # prepare callback for creating in new entities during options config flow
    entry.runtime_data.add_component_callbacks[Platform.SELECT.value] = (
        _create_select_entities
    )


@dataclass(frozen=True, kw_only=True)
class OpenEMSSelectDescription(SelectEntityDescription):
    """Defintion of OpenEMS sensor attributes."""

    has_entity_name = True


class OpenEMSSelectEntity(SelectEntity):
    """Select entity class for OpenEMS channels."""

    entity_description: OpenEMSSelectDescription

    def __init__(
        self,
        channel: OpenEMSEnumProperty,
        entity_description,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize OpenEMS switch entity."""
        self._channel: OpenEMSEnumProperty = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id()
        self._attr_device_info = device_info
        self._attr_should_poll = False
        self._attr_extra_state_attributes = channel.orig_json
        self._raw_value = None

    def handle_current_value(self, value) -> None:
        """Handle a state update."""
        if self._raw_value != value:
            previous_option = self.current_option
            self._raw_value = value
            if previous_option != self.current_option:
                self.async_schedule_update_ha_state()

    @property
    def current_option(self) -> str | None:
        """Return the current option."""
        if (
            isinstance(self._raw_value, str)
            and self._raw_value in self.entity_description.options
        ):
            return self._raw_value
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self._channel.update_value(option)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Entity created."""
        self._channel.register_callback(
            self.handle_current_value,
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Entity removed."""
        self._channel.unregister_callback()
        await super().async_will_remove_from_hass()
