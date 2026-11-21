"""Base entity of the channel based OpenEMS platforms."""

from openems_client.openems import OpenEMSDataHandler

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription


class OpenEMSChannelEntity[ChannelT: OpenEMSDataHandler](Entity):
    """Entity representing a channel of an OpenEMS component."""

    _attr_should_poll = False

    def __init__(
        self,
        channel: ChannelT,
        entity_description: EntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the entity."""
        self._channel: ChannelT = channel
        self.entity_description = entity_description
        self._attr_unique_id = channel.unique_id
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        """Return True while the edge is connected and sends channel data."""
        return super().available and self._channel.available

    @callback
    def _handle_channel_update(self) -> None:
        """Write the state after a value or availability change of the channel."""
        self.async_schedule_update_ha_state()

    async def async_added_to_hass(self) -> None:
        """Listen to the channel, which subscribes it at the edge."""
        self.async_on_remove(self._channel.add_listener(self._handle_channel_update))
        await super().async_added_to_hass()
