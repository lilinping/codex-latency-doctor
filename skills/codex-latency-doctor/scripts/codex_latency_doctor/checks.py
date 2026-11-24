"""Run opt-in, bounded network and native CLI checks."""

import ipaddress
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path


ENDPOINTS = {
    "chatgpt": "https://chatgpt.com/backend-api/codex/models",
    "api": "https://api.openai.com/v1/models",
}

FAILURES = {
    5: "proxy_dns_failure",
    6: "dns_failure",
    7: "connect_failure",
    28: "timeout",
    35: "tls_handshake_failure",
    60: "certificate_verification_failure",
    97: "proxy_handshake_failure",
}


def _windows_programs(*names: str) -> Iterator[Path]:
    # Python 3.11's shutil.which implicitly checks the working directory.
    for entry in os.get_exec_path():
        directory = Path(entry.strip('"'))
        if not directory.is_absolute():
            continue
        for name in names:
            program = directory / name
            if os.path.isfile(program):
                yield program


def _macos_https_proxy() -> tuple[str, str | None]:
    if sys.platform != "darwin":
        return "unsupported_platform", None
    try:
        result = subprocess.run(
            ["/usr/sbin/scutil", "--proxy"], capture_output=True,
            encoding="utf-8", errors="replace", timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "proxy_settings_unavailable", None
    if result.returncode:
        return "proxy_settings_unavailable", None
    # Only read top-level settings. Per-interface entries need route-specific resolution.
    fields = dict(re.findall(r"(?m)^ {2}([A-Za-z]+) : ([^\r\n]+)$", result.stdout))
    if any(fields.get(key) == "1" for key in ("ProxyAutoConfigEnable", "ProxyAutoDiscoveryEnable")):
        return "automatic_proxy_not_tested", None
    if fields.get("HTTPSEnable") != "1":
        return "no_fixed_https_proxy", None
    host, port = fields.get("HTTPSProxy", ""), fields.get("HTTPSPort", "")
    if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
        return "invalid_proxy_settings", None
    if ":" in host:
        try:
            address = ipaddress.IPv6Address(host.removeprefix("[").removesuffix("]"))
        except ipaddress.AddressValueError:
            return "invalid_proxy_settings", None
        if address.scope_id is not None:
            return "invalid_proxy_settings", None
        host = f"[{address}]"
    elif not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})", host):
        return "invalid_proxy_settings", None
    return "ok", f"http://{host}:{port}"


def _route(curl: str, endpoint: str, *, direct: bool = False, proxy: str | None = None) -> dict:
    flags = []
    if direct:
        flags = ["--noproxy", "*"]
    elif proxy is not None:
        flags = ["--proxy", proxy, "--noproxy", ""]
    command = [
        curl, "--disable", "--silent", "--output", os.devnull,
        "--connect-timeout", "3", "--max-time", "6",
        "--write-out", "%{http_code} %{time_starttransfer}",
        *flags, "--", endpoint,
    ]
    try:
        process = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="ascii", errors="replace", timeout=7, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except OSError:
        return {"status": "curl_unavailable"}
    if process.returncode:
        return {"status": FAILURES.get(process.returncode, "curl_failure")}
    try:
        code_text, time_text = process.stdout.split()
        code, seconds = int(code_text), float(time_text)
    except ValueError:
        return {"status": "unexpected_curl_output"}
    if not 100 <= code < 600 or seconds < 0 or not math.isfinite(seconds):
        return {"status": "unexpected_curl_output"}
    return {"status": "http_response", "http_status": code, "ttfb_ms": round(seconds * 1000, 1)}


def _summary(samples: list[dict]) -> dict:
    times = [sample["ttfb_ms"] for sample in samples if sample["status"] == "http_response"]
    return {
        "http_responses": len(times),
        "median_ttfb_ms": statistics.median(times) if times else None,
        "samples": samples,
    }


def probe_network(service: str, samples: int, *, compare_direct: bool, compare_macos_proxy: bool = False) -> dict:
    executable = next(_windows_programs("curl.exe"), None) if sys.platform == "win32" else shutil.which("curl")
    if not executable:
        return {"status": "curl_unavailable"}
    curl = str(executable)
    environment_route, direct_route, macos_route = [], [], []
    proxy_status, proxy = _macos_https_proxy() if compare_macos_proxy else (None, None)
    endpoint = ENDPOINTS[service]
    for _ in range(samples):
        environment_route.append(_route(curl, endpoint))
        if compare_direct:
            direct_route.append(_route(curl, endpoint, direct=True))
        if proxy is not None:
            macos_route.append(_route(curl, endpoint, proxy=proxy))
    routes = {"environment_route": _summary(environment_route)}
    if compare_direct:
        routes["direct_route"] = _summary(direct_route)
    if compare_macos_proxy:
        routes["macos_proxy_route"] = {"probe_status": proxy_status, **_summary(macos_route)}
    reached_http = any(route["http_responses"] for route in routes.values())
    return {"status": "ok" if reached_http else "no_http_response", "service": service, **routes}


def verify_native(codex_home: Path) -> dict:
    executable = (
        next(_windows_programs("codex.exe", "codex.cmd"), None)
        if sys.platform == "win32" else shutil.which("codex")
    )
    if not executable:
        return {"status": "codex_not_found"}
    command = [str(executable)]
    if sys.platform == "win32" and Path(executable).suffix.lower() == ".cmd":
        # Launch npm's official entrypoint directly to avoid cmd.exe parsing paths or arguments.
        directory = Path(executable).parent
        local_shim = directory.name.lower() == ".bin" and directory.parent.name.lower() == "node_modules"
        modules = directory.parent if local_shim else directory / "node_modules"
        entrypoint = modules / "@openai" / "codex" / "bin" / "codex.js"
        adjacent_node = directory / "node.exe"
        node = adjacent_node if os.path.isfile(adjacent_node) else next(_windows_programs("node.exe"), None)
        if node is None or not os.path.isfile(entrypoint):
            return {"status": "codex_not_runnable"}
        command = [str(node), str(entrypoint)]
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home.absolute())
    try:
        result = subprocess.run(
            [*command, "app-server", "--strict-config", "--print-startup-config"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=environment, timeout=15, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except OSError:
        return {"status": "codex_not_runnable"}
    return {"status": "accepted" if result.returncode == 0 else "rejected_or_unsupported"}
