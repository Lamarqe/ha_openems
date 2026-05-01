"""Helper methods which are independent of openems and HA classes."""

import uuid

from yarl import URL

from .const import CONN_TYPES, ConnectionType


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
