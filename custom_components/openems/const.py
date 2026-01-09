"""Constants for the HA OpenEMS integration."""

from typing import TypedDict
import uuid

from yarl import URL

SLASH_ESC = "_s_l_a_s_h_"

DOMAIN: str = "openems"

ATTR_VALUE: str = "value"
ATTR_TIMEOUT: str = "timeout"
ATTR_UPDATE_CYCLE: str = "update_cycle"

CONF_EDGES: str = "edges"
CONF_EDGE: str = "edge"

CONN_TYPE_DIRECT_EDGE: str = "direct_edge"
CONN_TYPE_LOCAL_FEMS: str = "local_fems"
CONN_TYPE_LOCAL_OPENEMS: str = "local_openems"
CONN_TYPE_WEB_FENECON: str = "web_fenecon"
CONN_TYPE_CUSTOM_URL: str = "custom_url"
CONN_TYPE_REST: str = "rest"

QUERY_CONFIG_VIA_REST: bool = False


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


def connection_url(type: str, host: str | None = None) -> URL:
    "Construct URL for the given type and host."
    default_params: ConnectionType = CONN_TYPES[type]
    if not (url_host := default_params["host"]):
        url_host = host
    return URL.build(
        scheme=default_params["scheme"],
        host="" if url_host is None else url_host,
        port=default_params["port"],
        path=default_params["path"],
    )


def wrap_jsonrpc(method: str, **params):
    """Wrap a method call with paramters into a jsonrpc call."""
    envelope = {}
    envelope["jsonrpc"] = "2.0"
    envelope["method"] = method
    envelope["params"] = params
    envelope["id"] = str(uuid.uuid4())
    return envelope
