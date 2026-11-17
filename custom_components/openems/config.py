"""Load Home Assistant specific entity options from json files."""

import json
from pathlib import Path
import re

ENCODING = "utf-8"


class OpenEMSConfig:
    """Load additional config options from json files."""

    def __init__(self) -> None:
        """Initialize and read json files."""
        path = Path(__file__).parent / "config"
        with (path / "default_channels.json").open(encoding=ENCODING) as channel_file:
            self.default_channels = json.load(channel_file)
        with (path / "number_modes.json").open(encoding=ENCODING) as number_file:
            self.number_modes = json.load(number_file)

    def _get_config_property(self, dict, property, component_name, channel_name):
        """Return dict property for a given component/channel."""
        for component_conf in dict:
            comp_regex = component_conf["component_regexp"]
            if re.fullmatch(comp_regex, component_name):
                for channel in component_conf["channels"]:
                    if channel["id"] == channel_name:
                        return channel.get(property)
        return None

    def get_number_mode(self, component_name, channel_name) -> str | None:
        """Return the display mode ("box" or "slider") for a given component/channel."""
        return self._get_config_property(
            self.number_modes, "mode", component_name, channel_name
        )

    def is_component_enabled(self, comp_name: str) -> bool:
        """Return if there is at least one channel enabled by default."""
        for entry in self.default_channels:
            if re.fullmatch(entry["component_regexp"], comp_name):
                return True

        return False

    def is_channel_enabled(self, comp_name, chan_name) -> bool:
        """Return True if the channel is enabled by default."""
        for entry in self.default_channels:
            if re.fullmatch(entry["component_regexp"], comp_name):
                if chan_name in entry["channels"]:
                    return True

        return False


CONFIG: OpenEMSConfig = OpenEMSConfig()
