# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Safety rules for a crawler that follows hostnames strangers gave it.

This monitor connects to addresses learned from ``server.peers.subscribe``.  That
is untrusted input from a party with an incentive to point it somewhere
interesting, so every hostname is validated, every resolved address is
classified, and anything private, local or otherwise not a public Internet host
is refused before a socket is opened.

The rule is deliberately blunt: **a public crawl only ever connects to global
unicast addresses.**  Development setups that need loopback pass an explicit
allowance, and that allowance is never derived from peer data.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable, List, Optional, Tuple

from .model import EndpointId, Limits, Transport

#: Hostname labels per RFC 1123, plus a trailing-dot tolerance.
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
_ONION = re.compile(r"^[a-z2-7]{16}\.onion$|^[a-z2-7]{56}\.onion$", re.IGNORECASE)

#: Ports an Electrum endpoint may plausibly use.  Anything else is refused
#: rather than probed, since a peer record is an invitation to connect somewhere.
MIN_PORT = 1
MAX_PORT = 65535

#: Cloud metadata endpoints, called out separately because they are the classic
#: SSRF target and are otherwise ordinary-looking global addresses.
METADATA_ADDRESSES = frozenset({
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
})


class UnsafeTarget(ValueError):
    """The target must not be probed."""


def normalize_hostname(value: object, limits: Optional[Limits] = None) -> str:
    """Validate and canonicalise a hostname from untrusted input."""
    limits = limits or Limits()
    if not isinstance(value, str):
        raise UnsafeTarget("hostname is not a string")
    hostname = value.strip().rstrip(".").lower()
    if not hostname:
        raise UnsafeTarget("hostname is empty")
    if len(hostname) > limits.max_hostname_length:
        raise UnsafeTarget("hostname is too long")
    if any(character.isspace() for character in hostname):
        raise UnsafeTarget("hostname contains whitespace")
    if "/" in hostname or "\\" in hostname or "@" in hostname or ":" in hostname:
        raise UnsafeTarget("hostname contains a separator, so it is not a bare host")
    if hostname.endswith(".onion"):
        if not _ONION.match(hostname):
            raise UnsafeTarget("onion address is malformed")
        return hostname
    # A bare IP literal is allowed only if it is a global unicast address; it is
    # classified here rather than after a DNS lookup that will not happen.
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        classify_address(address)
        return hostname
    labels = hostname.split(".")
    if len(labels) < 2:
        raise UnsafeTarget("hostname is not fully qualified")
    for label in labels:
        if not _LABEL.match(label):
            raise UnsafeTarget(f"hostname label {label!r} is invalid")
    return hostname


def normalize_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            raise UnsafeTarget("port is not an integer") from None
    if not MIN_PORT <= value <= MAX_PORT:
        raise UnsafeTarget(f"port {value} is out of range")
    return value


def classify_address(address: ipaddress._BaseAddress, *,
                     allow_private: bool = False) -> ipaddress._BaseAddress:
    """Refuse anything that is not a public Internet address."""
    if allow_private:
        return address
    if address in METADATA_ADDRESSES:
        raise UnsafeTarget(f"{address} is a cloud metadata address")
    if address.is_loopback:
        raise UnsafeTarget(f"{address} is loopback")
    if address.is_private:
        raise UnsafeTarget(f"{address} is a private address")
    if address.is_link_local:
        raise UnsafeTarget(f"{address} is link local")
    if address.is_multicast:
        raise UnsafeTarget(f"{address} is multicast")
    if address.is_reserved or address.is_unspecified:
        raise UnsafeTarget(f"{address} is reserved")
    if getattr(address, "is_site_local", False):
        raise UnsafeTarget(f"{address} is site local")
    if not address.is_global:
        raise UnsafeTarget(f"{address} is not globally routable")
    return address


def safe_resolved_addresses(addresses: Iterable[str], *,
                            allow_private: bool = False) -> List[str]:
    """Filter a DNS answer down to addresses that may be connected to.

    Every answer is checked, not just the first: a hostname that resolves to one
    public and one private address must not be probed on the private one, which
    is the shape a DNS rebinding attempt takes.
    """
    safe = []
    for candidate in addresses:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        try:
            classify_address(address, allow_private=allow_private)
        except UnsafeTarget:
            continue
        safe.append(str(address))
    return safe


def parse_peer_record(record: object, limits: Optional[Limits] = None) -> Tuple:
    """Parse one ``server.peers.subscribe`` entry into endpoints.

    The protocol shape is ``[ip, hostname, [features...]]`` where features carry
    ``s<port>`` for TLS, ``t<port>`` for plaintext and ``v<version>`` for the
    protocol version.  Anything that does not fit is dropped rather than guessed
    at, and the reported IP is ignored entirely: the hostname is the identity,
    and resolution happens here, not on the peer's say-so.
    """
    limits = limits or Limits()
    if not isinstance(record, (list, tuple)) or len(record) < 3:
        raise UnsafeTarget("peer record is not a triple")
    hostname = normalize_hostname(record[1], limits)
    features = record[2]
    if not isinstance(features, (list, tuple)):
        raise UnsafeTarget("peer features are not a list")
    if len(features) > 16:
        raise UnsafeTarget("peer advertises an implausible number of features")

    endpoints = []
    protocol_version = None
    for feature in features:
        if not isinstance(feature, str) or not feature or len(feature) > 24:
            continue
        kind, _, rest = feature[0], feature[1:2], feature[1:]
        if kind == "v":
            protocol_version = rest[:16]
            continue
        if kind not in ("s", "t"):
            continue
        transport = Transport.TLS if kind == "s" else Transport.TCP
        port_text = rest or ("50002" if kind == "s" else "50001")
        try:
            port = normalize_port(port_text)
        except UnsafeTarget:
            continue
        endpoints.append(EndpointId(hostname, port, transport))
    if not endpoints:
        raise UnsafeTarget("peer advertises no usable Electrum port")
    return tuple(endpoints), protocol_version


def parse_peers_response(response: object, limits: Optional[Limits] = None) -> List:
    """Parse a whole peers response, bounded and forgiving of individual junk.

    A hostile server can return thousands of entries; only the first
    ``max_peers_per_response`` are considered, and one malformed entry does not
    discard the rest.
    """
    limits = limits or Limits()
    if not isinstance(response, (list, tuple)):
        raise UnsafeTarget("peers response is not a list")
    discovered = []
    seen = set()
    for record in response[:limits.max_peers_per_response]:
        try:
            endpoints, _version = parse_peer_record(record, limits)
        except UnsafeTarget:
            continue
        for endpoint in endpoints:
            key = (endpoint.hostname, endpoint.port, endpoint.transport)
            if key in seen:
                continue
            seen.add(key)
            discovered.append(endpoint)
    return discovered
