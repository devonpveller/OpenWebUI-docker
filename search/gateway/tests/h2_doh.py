"""A DoH endpoint shaped like Mullvad's: TLS 1.3, HTTP/2 ONLY (harness item search-post-gate).

MEASURED live 2026-10-31 through the plane's VPN proxy (journal/evidence/search-post-gate/live/):
dns.mullvad.net and base.dns.mullvad.net negotiate ALPN ``h2`` when it is offered, and when it is
not (an HTTP/1.1-only client) they negotiate nothing and write an HTTP/2 SETTINGS frame anyway - an
HTTP/1.1 request is never answered (the client sees ``Server disconnected`` / ``illegal request
line``). This is that behaviour, in a thread, with a throwaway CA so a client can verify it:

  make_pki(dir)       a CA and a leaf for ``dns.mullvad.net`` (ca.pem, leaf.pem, leaf.key)
  H2DohServer         accepts TLS, speaks only HTTP/2, answers ``GET /dns-query?dns=`` from
                      tests/fakes.py's DNS_MAP; counts connections and records every query

A lab stand-in, not a security component. The proxy side is FakeInternet.tunnels (fakes.py).
"""

from __future__ import annotations

import base64
import datetime
import socket
import ssl
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import dns.message
import h2.config
import h2.connection
import h2.events
import h2.exceptions

from fakes import _doh_answer

DOH_HOST = "dns.mullvad.net"


def make_pki(dest: Path, host: str = DOH_HOST) -> None:
    """ca.pem + leaf.pem + leaf.key in `dest` (a CA that signs a leaf for `host`).

    `cryptography` is imported here only: the lab container (the gate image) serves, it never mints.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    def _name(cn: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(_name("search-post-gate lab CA"))
          .issuer_name(_name("search-post-gate lab CA")).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                         critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(_name(host)).issuer_name(ca.subject)
            .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    dest.mkdir(parents=True, exist_ok=True)
    pem = serialization.Encoding.PEM
    (dest / "ca.pem").write_bytes(ca.public_bytes(pem))
    (dest / "leaf.pem").write_bytes(leaf.public_bytes(pem))
    (dest / "leaf.key").write_bytes(leaf_key.private_bytes(
        pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


class H2DohServer:
    def __init__(self, pki: Path, host: str = "127.0.0.1", port: int = 0) -> None:
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        self._ctx.load_cert_chain(pki / "leaf.pem", pki / "leaf.key")
        self._ctx.set_alpn_protocols(["h2"])
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(64)
        self.port = self._sock.getsockname()[1]
        self.connections = 0       # TCP connections accepted
        self.alpn: list[str | None] = []
        self.queries: list[str] = []   # "name. IN A"
        self.http1_attempts = 0    # connections that spoke something other than HTTP/2
        self._stop = False
        self._t = threading.Thread(target=self._accept, daemon=True)

    def start(self) -> H2DohServer:
        self._t.start()
        return self

    def stop(self) -> None:
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass

    def _accept(self) -> None:
        while not self._stop:
            try:
                c, _ = self._sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, raw: socket.socket) -> None:
        try:
            raw.settimeout(15)
            tls = self._ctx.wrap_socket(raw, server_side=True)
            self.alpn.append(tls.selected_alpn_protocol())
            conn = h2.connection.H2Connection(h2.config.H2Configuration(client_side=False))
            conn.initiate_connection()
            tls.sendall(conn.data_to_send())  # Mullvad's first bytes: an HTTP/2 SETTINGS frame
            while True:
                data = tls.recv(65536)
                if not data:
                    return
                try:
                    events = conn.receive_data(data)
                except h2.exceptions.ProtocolError:
                    self.http1_attempts += 1   # an HTTP/1.1 request: never answered
                    return
                for ev in events:
                    if isinstance(ev, h2.events.RequestReceived):
                        self._answer(conn, ev)
                out = conn.data_to_send()
                if out:
                    tls.sendall(out)
        except (OSError, ssl.SSLError):
            return
        finally:
            try:
                raw.close()
            except OSError:
                pass

    def _answer(self, conn: h2.connection.H2Connection, ev: h2.events.RequestReceived) -> None:
        hdrs = {k.decode(): v.decode() for k, v in ev.headers}
        parts = urlsplit(hdrs.get(":path", ""))
        qs = parse_qs(parts.query)
        if hdrs.get(":method") != "GET" or parts.path != "/dns-query" or "dns" not in qs:
            conn.send_headers(ev.stream_id, [(":status", "400")], end_stream=True)
            return
        wire = base64.urlsafe_b64decode(qs["dns"][0] + "=" * (-len(qs["dns"][0]) % 4))
        q = dns.message.from_wire(wire).question[0]
        self.queries.append(q.to_text())
        body = _doh_answer(wire)
        conn.send_headers(ev.stream_id, [(":status", "200"),
                                         ("content-type", "application/dns-message"),
                                         ("content-length", str(len(body)))])
        conn.send_data(ev.stream_id, body, end_stream=True)
