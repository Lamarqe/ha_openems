"""Constants for the HA OpenEMS integration."""

from enum import StrEnum

DOMAIN: str = "openems"


class OpenEMSService(StrEnum):
    """Actions of the OpenEMS integration."""

    UPDATE_COMPONENT_CONFIG = "update_component_config"
    WRITE_CHANNEL = "write_channel"


class OpenEMSServiceArgument(StrEnum):
    """Fields of the OpenEMS actions."""

    PROPERTIES = "properties"
    CHANNEL = "channel"
    VALUE = "value"
    UPDATE_CYCLE = "update_cycle"
    TIMEOUT = "timeout"


CONF_EDGES: str = "edges"
CONF_EDGE: str = "edge"
CONF_MORE_OPTIONS: str = "more_options"
CONF_ADVANCED_OPTIONS: str = "advanced_options"
CONF_FORWARD_INTERVAL: str = "forward_interval"
CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: str = (
    "ignore_decreasing_if_total_increasing"
)

QUERY_CONFIG_VIA_REST: bool = False
