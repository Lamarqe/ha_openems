"""Constants for the HA OpenEMS integration."""

DOMAIN: str = "openems"

ATTR_VALUE: str = "value"
ATTR_PROPERTIES: str = "properties"
ATTR_TIMEOUT: str = "timeout"
ATTR_UPDATE_CYCLE: str = "update_cycle"

CONF_EDGES: str = "edges"
CONF_EDGE: str = "edge"
CONF_MORE_OPTIONS: str = "more_options"
CONF_ADVANCED_OPTIONS: str = "advanced_options"
CONF_FORWARD_INTERVAL: str = "forward_interval"
CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: str = (
    "ignore_decreasing_if_total_increasing"
)

QUERY_CONFIG_VIA_REST: bool = False
