"""Small transport adapters: validate and pin DNS at connection establishment.

HTTPcore/urllib3 retain the original hostname for HTTP Host, TLS SNI and certificate
checks. Only the TCP destination is replaced. Keep the integration tests when
upgrading these version-pinned connection hooks.
"""

import ipaddress
import os
import socket

import httpcore
import httpx
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError
from urllib3.util.connection import create_connection


def addresses(host, port):
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not ips:
        raise OSError("No addresses returned for host.")
    if os.getenv("FEEDELIO_ALLOW_PRIVATE_NETWORK") != "1" and any(
        not ipaddress.ip_address(ip).is_global for ip in ips
    ):
        raise ValueError(
            "Private network addresses are disabled. Set FEEDELIO_ALLOW_PRIVATE_NETWORK=1 to opt in."
        )
    return ips


def check_proxy(proxy):
    if proxy and os.getenv("FEEDELIO_ALLOW_PRIVATE_NETWORK") != "1":
        raise ValueError(
            "A proxy resolves target addresses itself; use only a trusted proxy with FEEDELIO_ALLOW_PRIVATE_NETWORK=1."
        )


class PinnedBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        error = None
        for ip in addresses(host, port):
            try:
                return super().connect_tcp(ip, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                error = exc
        raise error


def http_client(options=None, verify=True, **kwargs):
    proxy = (options or {}).get("proxy") or None
    check_proxy(proxy)
    transport = httpx.HTTPTransport(proxy=proxy, trust_env=False, verify=verify)
    transport._pool._network_backend = PinnedBackend()
    return httpx.Client(transport=transport, trust_env=False, follow_redirects=False, **kwargs)


class PinnedConnection:
    def _new_conn(self):
        try:
            error = None
            for ip in addresses(self._dns_host, self.port):
                try:
                    return create_connection(
                        (ip, self.port),
                        self.timeout,
                        source_address=self.source_address,
                        socket_options=self.socket_options,
                    )
                except OSError as exc:
                    error = exc
            raise error
        except socket.timeout as exc:
            raise ConnectTimeoutError(self, "Connection timed out") from exc
        except OSError as exc:
            raise NewConnectionError(self, str(exc)) from exc


class PinnedHTTPConnection(PinnedConnection, HTTPConnection):
    pass


class PinnedHTTPSConnection(PinnedConnection, HTTPSConnection):
    pass


class PinnedHTTPPool(HTTPConnectionPool):
    ConnectionCls = PinnedHTTPConnection


class PinnedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = PinnedHTTPSConnection


class PinnedAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": PinnedHTTPPool, "https": PinnedHTTPSPool}

    def proxy_manager_for(self, proxy, **kwargs):
        check_proxy(proxy)
        return super().proxy_manager_for(proxy, **kwargs)
