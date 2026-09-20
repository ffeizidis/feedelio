"""Small transport adapters: validate and pin DNS at connection establishment.

HTTPcore/urllib3 retain the original hostname for HTTP Host, TLS SNI and certificate
checks. Only the TCP destination is replaced. Keep the integration tests when
upgrading these version-pinned connection hooks.
"""

import ipaddress
import os
import socket
import time

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


def attempts(host, port, timeout):
    """Yield (ip, timeout) pairs sharing one connect budget across all addresses.

    A dead host behind a dozen A records would otherwise cost a full timeout
    per address and stall the worker for minutes.
    """
    if not isinstance(timeout, (int, float)):  # None, or urllib3's default sentinel
        timeout = None
    deadline = None if timeout is None else time.monotonic() + timeout
    for ip in addresses(host, port):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise socket.timeout("Connection timed out")
        yield ip, remaining


def check_proxy(proxy):
    if proxy and os.getenv("FEEDELIO_ALLOW_PRIVATE_NETWORK") != "1":
        raise ValueError(
            "A proxy resolves target addresses itself; use only a trusted proxy with FEEDELIO_ALLOW_PRIVATE_NETWORK=1."
        )


class PinnedBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        error = None
        try:
            for ip, remaining in attempts(host, port, timeout):
                try:
                    return super().connect_tcp(ip, port, remaining, local_address, socket_options)
                except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                    error = exc
        except socket.timeout as exc:
            raise httpcore.ConnectTimeout(str(exc)) from exc
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
            for ip, remaining in attempts(self._dns_host, self.port, self.timeout):
                try:
                    return create_connection(
                        (ip, self.port),
                        remaining,
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
