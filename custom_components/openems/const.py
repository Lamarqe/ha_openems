"""Constants for the HA OpenEMS integration."""

from typing import TypedDict

SLASH_ESC = "_s_l_a_s_h_"

DOMAIN: str = "openems"

ATTR_VALUE: str = "value"
ATTR_TIMEOUT: str = "timeout"
ATTR_UPDATE_CYCLE: str = "update_cycle"

CONF_EDGES: str = "edges"
CONF_EDGE: str = "edge"
CONF_COMPONENTS: str = "components"
CONF_ADVANCED_OPTIONS: str = "advanced_options"
CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: str = (
    "ignore_decreasing_if_total_increasing"
)
CONF_FORWARD_INTERVAL: str = "forward_interval"

CONN_TYPE_DIRECT_EDGE: str = "direct_edge"
CONN_TYPE_LOCAL_FEMS: str = "local_fems"
CONN_TYPE_LOCAL_OPENEMS: str = "local_openems"
CONN_TYPE_WEB_FENECON: str = "web_fenecon"
CONN_TYPE_CUSTOM_URL: str = "custom_url"
CONN_TYPE_REST: str = "rest"

QUERY_CONFIG_VIA_REST: bool = False

CURRENT_DATA_TIMEOUT_SECONDS = 60


class AdvancedOptions(TypedDict):
    """Type containing the advanced options."""

    # Right after a restart, for some systems, the FEMS backend reports zero or unexplainably
    # small values for total amount channels. This settles typically after only a few seconds.
    # This parameter allows to ignore such values.
    ignore_decreasing_if_total_increasing: bool
    # 0 = forward currentData updates immediately; >0 = forward at most every N seconds.
    forward_interval: int


class ConfigOptions(TypedDict):
    """Type containing the config entry options."""

    components: dict[str, bool]
    advanced_options: AdvancedOptions


class ConnectionType(TypedDict):
    "Type containing the websocket connection paramters."

    scheme: str
    host: str | None
    port: int
    path: str


CONN_TYPES: dict[str, ConnectionType] = {
    CONN_TYPE_DIRECT_EDGE: {
        "scheme": "ws",
        "host": None,
        "port": 8085,
        "path": "/",
    },
    CONN_TYPE_LOCAL_FEMS: {
        "scheme": "ws",
        "host": None,
        "port": 80,
        "path": "/websocket",
    },
    CONN_TYPE_LOCAL_OPENEMS: {
        "scheme": "ws",
        "host": None,
        "port": 8082,
        "path": "/",
    },
    CONN_TYPE_WEB_FENECON: {
        "scheme": "wss",
        "host": "portal.fenecon.de",
        "port": 443,
        "path": "/openems-backend-ui2",
    },
    CONN_TYPE_REST: {
        "scheme": "http",
        "host": None,
        "port": 8084,
        "path": "/rest/",
    },
}
