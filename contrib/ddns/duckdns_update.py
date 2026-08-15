#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Keep a DuckDNS hostname pointed at this host's current public address.

The DuckDNS update API detects the caller's public IPv4 by itself when the ``ip``
parameter is empty, so this updater never contacts a third-party IP-discovery
service for IPv4.  An optional IPv6 address is read from this host's own
interfaces, or supplied explicitly.

The account token is read from a file, never from the command line, and never
appears in log output or in an error message.  It unavoidably appears inside the
request URL, because that is the API DuckDNS publishes, so the URL itself is
never logged.

API reference: https://www.duckdns.org/spec.jsp
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

UPDATE_ENDPOINT = "https://www.duckdns.org/update"
DEFAULT_TIMEOUT = 30
LABEL_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
REDACTED = "<redacted>"

logger = logging.getLogger("duckdns")


class DuckDnsError(RuntimeError):
    """A DuckDNS update could not be completed."""


def redact(text: str, token: str) -> str:
    """Remove the token from any text before it reaches a log or an exception."""
    if token:
        text = text.replace(token, REDACTED)
        text = text.replace(urllib.parse.quote(token, safe=""), REDACTED)
    return text


def validate_domains(value: str) -> str:
    """Accept the DuckDNS subname form, not a full hostname or a URL."""
    if not value or value != value.strip():
        raise DuckDnsError("DuckDNS domain is empty or padded with whitespace")
    labels = value.split(",")
    for label in labels:
        candidate = label.strip()
        if candidate != label:
            raise DuckDnsError(f"DuckDNS domain {label!r} has surrounding whitespace")
        if candidate.endswith(".duckdns.org"):
            raise DuckDnsError(
                f"use only the subname, not {candidate!r}; "
                f"for example 'my-ravencoin-node'"
            )
        if "." in candidate or "/" in candidate or ":" in candidate:
            raise DuckDnsError(f"DuckDNS domain {candidate!r} must be a single label")
        if not LABEL_PATTERN.match(candidate):
            raise DuckDnsError(
                f"DuckDNS domain {candidate!r} must be 1-63 characters of "
                f"lowercase letters, digits or hyphens, and may not start or "
                f"end with a hyphen"
            )
    return ",".join(labels)


def read_token(path: Path) -> str:
    """Read the token from a private file and refuse obviously unsafe input."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DuckDnsError(f"token file {path} does not exist") from exc
    except PermissionError as exc:
        raise DuckDnsError(f"token file {path} is not readable") from exc
    token = raw.strip()
    if not token:
        raise DuckDnsError(f"token file {path} is empty")
    if len(token.splitlines()) != 1:
        raise DuckDnsError(f"token file {path} must contain exactly one line")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", token):
        raise DuckDnsError(f"token file {path} does not contain a plausible token")
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = None
    if mode is not None and mode & 0o077:
        logger.warning("token file %s is group or world accessible (mode %o); "
                       "run: chmod 600 %s", path, mode, path)
    return token


def detect_global_ipv6() -> Optional[str]:
    """Return a global-scope IPv6 address of this host, if one exists."""
    try:
        completed = subprocess.run(
            ["ip", "-6", "-oneline", "addr", "show", "scope", "global"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("cannot enumerate IPv6 addresses: %s", exc)
        return None
    if completed.returncode != 0:
        logger.info("no global IPv6 address available")
        return None
    for line in completed.stdout.splitlines():
        match = re.search(r"inet6\s+([0-9a-f:]+)/", line)
        if not match:
            continue
        candidate = match.group(1)
        if "temporary" in line or "deprecated" in line:
            continue
        if validate_ipv6(candidate, strict=False):
            return candidate
    logger.info("no usable global IPv6 address found")
    return None


def validate_ipv6(value: str, *, strict: bool = True) -> Optional[str]:
    try:
        socket.inet_pton(socket.AF_INET6, value)
    except OSError as exc:
        if strict:
            raise DuckDnsError(f"{value!r} is not a valid IPv6 address") from exc
        return None
    return value


def build_url(domains: str, token: str, ipv6: Optional[str]) -> str:
    """Build the documented update URL.  Never log the result."""
    query = {"domains": domains, "token": token, "ip": "", "verbose": "true"}
    if ipv6:
        query["ipv6"] = ipv6
    return f"{UPDATE_ENDPOINT}?{urllib.parse.urlencode(query)}"


def parse_response(body: str) -> dict:
    """Decode the verbose response: OK/KO, IPv4, IPv6, UPDATED/NOCHANGE."""
    lines = [line.strip() for line in body.strip().splitlines()]
    if not lines or lines[0] not in ("OK", "KO"):
        raise DuckDnsError("DuckDNS returned an unrecognized response")
    if lines[0] == "KO":
        raise DuckDnsError(
            "DuckDNS rejected the update; check that the domain belongs to this "
            "account and that the token is current"
        )
    return {
        "result": "OK",
        "ipv4": lines[1] if len(lines) > 1 else "",
        "ipv6": lines[2] if len(lines) > 2 else "",
        "status": lines[3] if len(lines) > 3 else "",
    }


def send_update(url: str, token: str, timeout: int, opener=None) -> str:
    """Perform the HTTPS request, mapping every failure to DuckDnsError."""
    request = urllib.request.Request(url, headers={"User-Agent": "electrumx-rvn-ddns"})
    open_func = opener if opener is not None else urllib.request.urlopen
    try:
        with open_func(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise DuckDnsError(
            redact(f"DuckDNS returned HTTP {exc.code}", token)
        ) from None
    except (urllib.error.URLError, OSError) as exc:
        raise DuckDnsError(
            redact(f"DuckDNS is unreachable: {exc}", token)
        ) from None


def write_state(path: Optional[Path], payload: dict) -> None:
    """Record the last outcome for humans.  Contains no credential."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n",
                             encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        logger.info("cannot write state file %s: %s", path, exc)


def update(domains: str, token_file: Path, ipv6_mode: str,
           timeout: int = DEFAULT_TIMEOUT, state_file: Optional[Path] = None,
           opener=None) -> dict:
    domains = validate_domains(domains)
    token = read_token(token_file)

    ipv6: Optional[str] = None
    if ipv6_mode == "auto":
        ipv6 = detect_global_ipv6()
    elif ipv6_mode not in ("", "off", None):
        ipv6 = validate_ipv6(ipv6_mode)

    try:
        body = send_update(build_url(domains, token, ipv6), token, timeout, opener)
        outcome = parse_response(body)
    except DuckDnsError as exc:
        message = redact(str(exc), token)
        write_state(state_file, {"result": "ERROR", "detail": message})
        raise DuckDnsError(message) from None

    logger.info("DuckDNS %s: %s ipv4=%s ipv6=%s",
                domains, outcome["status"] or "OK",
                outcome["ipv4"] or "unchanged-or-empty",
                outcome["ipv6"] or "none")
    write_state(state_file, {"result": "OK", **outcome, "domains": domains})
    return outcome


def parse_arguments(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update a DuckDNS hostname to this host's public address.")
    parser.add_argument("--domain", default=os.environ.get("DUCKDNS_DOMAIN", ""),
                        help="DuckDNS subname without .duckdns.org, "
                             "or a comma separated list")
    parser.add_argument("--token-file",
                        default=os.environ.get("DUCKDNS_TOKEN_FILE",
                                               ".secrets/duckdns_token"),
                        help="path to the private file holding the account token")
    parser.add_argument("--ipv6", default=os.environ.get("DUCKDNS_IPV6", "off"),
                        help="'off' (default), 'auto', or an explicit IPv6 address")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--state-file",
                        default=os.environ.get("DUCKDNS_STATE_FILE", ""),
                        help="optional path for the last-outcome record")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    arguments = parse_arguments(argv)
    if not arguments.domain:
        logger.error("no DuckDNS domain configured; set DUCKDNS_DOMAIN or --domain")
        return 2
    state_file = Path(arguments.state_file) if arguments.state_file else None
    try:
        update(arguments.domain, Path(arguments.token_file), arguments.ipv6,
               arguments.timeout, state_file)
    except DuckDnsError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
