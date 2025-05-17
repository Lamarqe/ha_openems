"""Constants for the HA OpenEMS integration."""

from typing import TypedDict

from yarl import URL

DOMAIN = "openems"

STORAGE_VERSION = 1

CONN_TYPE_DIRECT_EDGE = "direct_edge"
CONN_TYPE_LOCAL_FEMS = "local_fems"
CONN_TYPE_LOCAL_OPENEMS = "local_openems"
CONN_TYPE_WEB_FENECON = "web_fenecon"
CONN_TYPE_REST = "rest"


class ConnectionType(TypedDict):
    "Type containing the websocket connection paramters."

    scheme: str
    host: str | None
    port: int
    path: str


CONN_TYPES: dict[str:ConnectionType] = {
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
        host=url_host,
        port=default_params["port"],
        path=default_params["path"],
    )
