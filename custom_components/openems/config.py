"""Load additional config options from json files."""

import json
import os
import re
from typing import Any


class OpenEMSConfig:
    """Load additional config options from json files."""

    def __init__(self) -> None:
        """Initialize and read json files."""
        path = os.path.dirname(__file__)
        with open(
            path + "/config/default_channels.json", encoding="utf-8"
        ) as channel_file:
            self.default_channels = json.load(channel_file)
        with open(path + "/config/enum_options.json", encoding="utf-8") as enum_file:
            self.enum_options = json.load(enum_file)
        with open(path + "/config/time_options.json", encoding="utf-8") as time_file:
            self.time_options = json.load(time_file)
        with open(
            path + "/config/number_properties.json", encoding="utf-8"
        ) as number_file:
            self.number_properties = json.load(number_file)
        with open(
            path + "/config/component_update_groups.json", encoding="utf-8"
        ) as groups_file:
            self.update_groups = json.load(groups_file)

    def _get_config_property(self, dict, property, component_name, channel_name):
        """Return dict property for a given component/channel."""
        for component_conf in dict:
            comp_regex = component_conf["component_regexp"]
            if re.fullmatch(comp_regex, component_name):
                for channel in component_conf["channels"]:
                    if channel["id"] == channel_name:
                        return channel.get(property)
        return None

    def get_enum_options(self, component_name, channel_name) -> list[str] | None:
        """Return option string list for a given component/channel."""
        return self._get_config_property(
            self.enum_options, "options", component_name, channel_name
        )

    def is_time_property(self, component_name, channel_name) -> list[str] | None:
        """Return True if given component/channel is marked as time."""
        return self._get_config_property(
            self.time_options, "is_time", component_name, channel_name
        )

    def get_number_limit(self, component_name, channel_name) -> dict | None:
        """Return limit definition for a given component/channel."""
        return self._get_config_property(
            self.number_properties, "limit", component_name, channel_name
        )

    def get_number_multiplier(self, component_name, channel_name) -> dict | None:
        """Return multiplier for a given component/channel."""
        return self._get_config_property(
            self.number_properties, "multiplier", component_name, channel_name
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

    def update_group_members(self, comp_name, chan_name) -> tuple[list[str], Any]:
        """Return list of all update group members and the condition value."""
        for entry in self.update_groups:
            if re.fullmatch(entry["component_regexp"], comp_name):
                for rule in entry["rules"]:
                    if rule["channel"] == chan_name:
                        return rule["requires"], rule.get("when")

        return [], None
