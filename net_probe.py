"""Network path to LLM / harness backends: DNS, TCP, proxy CONNECT, TLS, time-to-first-byte.

Also reports the certificate issuer (a corporate CA such as Zscaler/Netskope means TLS
inspection) and whether HTTP/2 can be negotiated (some inspecting proxies only offer
HTTP/1.1, which can break or buffer streaming).
"""

import argparse
import socket
import ssl
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from harnessbench import runs, stats

DEFAULT_HOSTS = [
    "api.anthropic.com",
    "api.openai.com",
    "openrouter.ai",
    "generativelanguage.googleapis.com",
    "api2.cursor.sh",
    "github.com",
]
INSPECTION_VENDORS = [
    "Zscaler",
    "Netskope",
    "Palo Alto",
    "Fortinet",
    "Forcepoint",
    "Blue Coat",
    "Symantec",
    "McAfee",
    "Cisco",
    "Sophos",
    "Check Point",
    "Menlo",
]


def elapsed_ms(start_ns: int) -> float:
    return (time.perf_counter_ns() - start_ns) / 1e6


def https_proxy(host: str) -> tuple[str, int] | None:
    if urllib.request.proxy_bypass(host):
        return None
    url = urllib.request.getproxies().get("https")
    if not url:
        return None
    parsed = urlsplit(url if "://" in url else f"http://{url}")
    return parsed.hostname or "", parsed.port or 8080


def open_tcp(
    host: str, proxy: tuple[str, int] | None, timeout: float, result: dict
) -> socket.socket:
    target, port = proxy or (host, 443)
    start = time.perf_counter_ns()
    family, kind, proto, _, address = socket.getaddrinfo(
        target, port, type=socket.SOCK_STREAM
    )[0]
    result["dns_ms"] = elapsed_ms(start)
    sock = socket.socket(family, kind, proto)
    sock.settimeout(timeout)
    start = time.perf_counter_ns()
    sock.connect(address)
    result["tcp_ms"] = elapsed_ms(start)
    if proxy:
        start = time.perf_counter_ns()
        sock.sendall(
            f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode()
        )
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = sock.recv(4096)
            if not chunk:
                break
            reply += chunk
        result["proxy_connect_ms"] = elapsed_ms(start)
        status = reply.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 200" not in status:
            sock.close()
            raise ConnectionError(f"proxy CONNECT failed: {status}")
    return sock


def describe_issuer(cert: dict) -> str:
    fields = dict(item[0] for item in cert.get("issuer", ()))
    return " / ".join(
        v for v in (fields.get("organizationName"), fields.get("commonName")) if v
    )


def issuer_from_der(der: bytes) -> str:
    """Decode an unverified certificate's issuer via CPython's test helper, else sniff vendors."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pem = Path(tmp) / "cert.pem"
            pem.write_text(ssl.DER_cert_to_PEM_cert(der), encoding="ascii")
            return describe_issuer(ssl._ssl._test_decode_cert(str(pem)))  # type: ignore[attr-defined]
    except (AttributeError, ssl.SSLError, OSError, ValueError):
        found = [vendor for vendor in INSPECTION_VENDORS if vendor.encode() in der]
        return f"unknown (mentions: {', '.join(found)})" if found else "unknown"


def tls_connect(
    host: str, args: argparse.Namespace, alpn: list[str], verify: bool, result: dict
) -> ssl.SSLSocket:
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(alpn)
    sock = open_tcp(host, https_proxy(host), args.timeout, result)
    start = time.perf_counter_ns()
    try:
        tls = context.wrap_socket(sock, server_hostname=host)
    except Exception:
        sock.close()
        raise
    result["tls_ms"] = elapsed_ms(start)
    result["alpn"] = tls.selected_alpn_protocol()
    result["verified"] = verify
    result["issuer"] = (
        describe_issuer(tls.getpeercert())
        if verify
        else issuer_from_der(tls.getpeercert(True))
    )
    return tls


def measure(host: str, args: argparse.Namespace, alpn: list[str]) -> dict:
    result: dict = {
        "kind": "net",
        "env": args.env,
        "tag": args.tag,
        "host": host,
        "proxy": https_proxy(host),
    }
    try:
        try:
            tls = tls_connect(host, args, alpn, True, result)
        except ssl.SSLCertVerificationError as exc:
            result["verify_error"] = str(exc)[:200]
            tls = tls_connect(host, args, alpn, False, result)
        with tls:
            if result["alpn"] == "h2":
                return result
            start = time.perf_counter_ns()
            tls.sendall(
                f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
            )
            first = tls.recv(1024)
            result["ttfb_ms"] = elapsed_ms(start)
            result["status"] = first.split(b"\r\n", 1)[0].decode(errors="replace")
    except (OSError, ConnectionError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return result


def print_summary(records: list[dict], h2: dict[str, str | None]) -> None:
    rows = []
    for host, group in stats.group_by(records, lambda r: (r["host"],)).items():
        host = host[0]
        ok = [r for r in group if not r.get("error")]
        last = ok[-1] if ok else group[-1]
        rows.append(
            [
                host,
                str(len(ok)),
                stats.fmt(stats.median(r.get("dns_ms") for r in ok), 1),
                stats.fmt(stats.median(r.get("tcp_ms") for r in ok), 1),
                stats.fmt(stats.median(r.get("proxy_connect_ms") for r in ok), 1),
                stats.fmt(stats.median(r.get("tls_ms") for r in ok), 1),
                stats.fmt(stats.median(r.get("ttfb_ms") for r in ok), 1),
                str(h2.get(host)),
                "yes" if last.get("verified") else "NO",
                (last.get("issuer") or last.get("error") or "")[:60],
            ]
        )
    headers = [
        "host",
        "ok",
        "dns",
        "tcp",
        "proxy",
        "tls",
        "ttfb",
        "alpn(h2?)",
        "verified",
        "issuer / error",
    ]
    print(stats.render_table(headers, rows))
    print("medians in ms; first-rep DNS is often slower (cache miss)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=runs.default_env())
    parser.add_argument("--tag", default="manual")
    parser.add_argument("--hosts", nargs="*", default=DEFAULT_HOSTS)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=15)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out = runs.kind_file("net", args.env, args.tag)
    runs.append_jsonl(
        out,
        {"kind": "meta", "env": args.env, "tag": args.tag, "host": runs.host_info()},
    )
    h2 = {}
    for host in args.hosts:
        probe = measure(host, args, ["h2", "http/1.1"])
        h2[host] = probe.get("alpn") or probe.get("error", "")[:20]
    records = []
    for _ in range(args.reps):
        for host in args.hosts:
            record = measure(host, args, ["http/1.1"])
            records.append(record)
            runs.append_jsonl(out, record)
    print(f"writing {out}")
    print_summary(records, h2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
