from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from moonraker.confighelper import ConfigHelper


class SpoolmanProxy:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.http_client = self.server.lookup_component('http_client')
        self.klippy_apis = self.server.lookup_component('klippy_apis')

        orig_url = config.get('server')
        url_match = re.match(r"(?i:(?P<scheme>https?)://)?(?P<host>.+)", orig_url)
        if url_match is None:
            raise config.error(f"[spoolman_proxy] invalid server url: {orig_url}")
        scheme = url_match.group("scheme") or "http"
        host = url_match.group("host").rstrip("/")
        self.spoolman_url = f"{scheme}://{host}"

        self.server.register_remote_method("spoolman_proxy", self.rpc_spoolman_proxy)
        logging.info(f"SpoolmanProxy loaded, server: {self.spoolman_url}")

    async def rpc_spoolman_proxy(
        self,
        cb_endpoint: str,
        request_method: str,
        path: str,
        query: str | None = None,
        body: str | None = None,
    ) -> None:
        query_str = f"?{query}" if query else ""
        full_url = f"{self.spoolman_url}{path}{query_str}"
        logging.info(f"SpoolmanProxy: {request_method} {full_url}")

        response = await self.http_client.request(
            method=request_method,
            url=full_url,
            body=body,
        )

        if response.has_error():
            payload = None
            error = {"status_code": response.status_code, "message": str(response.error)}
            logging.warning(f"SpoolmanProxy error: {json.dumps(error)}")
        else:
            try:
                payload = response.json()
            except Exception:
                payload = None
            error = None

        await self.klippy_apis._send_klippy_request(
            cb_endpoint,
            {"payload": payload, "error": error},
            default=None,
        )


def load_component(config: ConfigHelper) -> SpoolmanProxy:
    return SpoolmanProxy(config)
