"""DNS pinning, proxy boundaries and real TLS hostname verification."""

import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpcore
import httpx
import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from feedelio.core import network


@pytest.mark.parametrize("transport", ["httpcore", "urllib3"])
def test_transport_connects_to_checked_ip_not_reresolved_host(monkeypatch, transport):
    monkeypatch.delenv("FEEDELIO_ALLOW_PRIVATE_NETWORK", raising=False)
    resolved, connected = [], []

    def resolve(host, port, **kwargs):
        resolved.append(host)
        # A second resolution of the original hostname would return localhost.
        ip = "8.8.8.8" if len(resolved) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    def connect(host, *args, **kwargs):
        connected.append(host)
        return object()

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    if transport == "httpcore":
        monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", lambda self, host, *args: connect(host))
        network.PinnedBackend().connect_tcp("rebinding.test", 80)
        assert connected == ["8.8.8.8"]
    else:
        monkeypatch.setattr(network, "create_connection", connect)
        connection = network.PinnedHTTPSConnection("rebinding.test", 443)
        connection._new_conn()
        assert connected == [("8.8.8.8", 443)]
        assert connection.host == "rebinding.test"
    assert resolved == ["rebinding.test"]


@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_mixed_dns_answers_fail_before_any_connection(monkeypatch, ip):
    monkeypatch.delenv("FEEDELIO_ALLOW_PRIVATE_NETWORK", raising=False)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80)) for address in ("8.8.8.8", ip)
        ],
    )
    monkeypatch.setattr(
        httpcore.SyncBackend, "connect_tcp", lambda *a: pytest.fail("Connected before validation")
    )
    with pytest.raises(ValueError, match="Private network"):
        network.PinnedBackend().connect_tcp("mixed.test", 80)


def test_proxy_requires_explicit_network_opt_in(monkeypatch):
    monkeypatch.delenv("FEEDELIO_ALLOW_PRIVATE_NETWORK", raising=False)
    with pytest.raises(ValueError, match="trusted proxy"):
        network.http_client({"proxy": "http://proxy.example:8080"})
    with pytest.raises(ValueError, match="trusted proxy"):
        network.PinnedAdapter().proxy_manager_for("http://proxy.example:8080")


@pytest.fixture
def tls_site(tmp_path, monkeypatch):
    monkeypatch.setenv("FEEDELIO_ALLOW_PRIVATE_NETWORK", "1")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "publisher.test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("publisher.test")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    sni, hosts, lookups = [], [], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hosts.append(self.headers["Host"])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"TLS publisher")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    context.set_servername_callback(lambda sock, name, ctx: sni.append(name))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host in ("publisher.test", "wrong.test"):
            lookups.append(host)
            host = "127.0.0.1"
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, cert_path, sni, hosts, lookups
    server.shutdown()
    server.server_close()
    thread.join(5)


@pytest.mark.parametrize("transport", ["httpx", "requests"])
def test_real_tls_keeps_sni_host_and_certificate_verification(tls_site, transport):
    port, cert, sni, hosts, lookups = tls_site
    url = f"https://publisher.test:{port}/"
    if transport == "httpx":
        with network.http_client(verify=ssl.create_default_context(cafile=str(cert))) as client:
            assert client.get(url).text == "TLS publisher"
            with pytest.raises(httpx.ConnectError, match="[Hh]ostname mismatch"):
                client.get(url.replace("publisher.test", "wrong.test"))
    else:
        with requests.Session() as client:
            client.trust_env = False
            client.mount("https://", network.PinnedAdapter())
            assert client.get(url, verify=str(cert)).text == "TLS publisher"
            with pytest.raises(requests.exceptions.SSLError):
                client.get(url.replace("publisher.test", "wrong.test"), verify=str(cert))
    assert sni == ["publisher.test", "wrong.test"]
    assert hosts == [f"publisher.test:{port}"]
    assert lookups == ["publisher.test", "wrong.test"]
