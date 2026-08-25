# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Signed observation bundles for multi-vantage observers.

An observer is one independently deployed instance of this monitor (EU,
US, Asia, a home node...).  Each observer crawls from its own network
position and publishes what it saw as a signed JSON bundle.  The
signature proves authorship and integrity; it grants no authority.  An
aggregator only accepts signatures from observer keys it was explicitly
configured to trust: an observation signing its own public key does not
make that key trusted, for exactly the same reason a server's
self-report is a claim and not a proof.

Operator diversity and observer diversity are two different axes and
never collapse into one: three observers measuring one CIPIG endpoint
are three vantage points over ONE Ravencoin operator.

Private keys never appear here: signing takes a cryptography Ed25519
private key object supplied by the caller, and nothing in a bundle
contains secrets, RPC credentials or local filesystem data.
"""

from __future__ import annotations

import base64
import datetime
import json
from typing import Dict, List, Mapping, Optional

from .model import EndpointId

SCHEMA_VERSION = 1
SIGNATURE_DOMAIN = b"RAVENCOIN-NETWORK-OBSERVER-OBSERVATION-v1\x00"

#: How far in the FUTURE a bundle timestamp may sit relative to the
#: verifier's clock.  Large enough to survive ordinary NTP skew between
#: unrelated networks.  Age is deliberately NOT bounded by skew: old
#: observations are bounded by ``expiresAt`` and by
#: ``DEFAULT_MAX_BUNDLE_AGE_SECONDS``, and a bundle that is still
#: within both has value (a vantage point that uploads hourly, for
#: example) and must not be discarded merely for being minutes old.
#: Replay of anything old is closed by the per-observer sequence
#: high-water mark, not by the skew window.
DEFAULT_MAX_CLOCK_SKEW_SECONDS = 300

#: A bundle older than this is refused no matter what its expiry says,
#: so a signed-but-retired observer can never keep old state alive.
DEFAULT_MAX_BUNDLE_AGE_SECONDS = 24 * 3600


class ObservationError(ValueError):
    """The observation bundle is unusable: malformed, untrusted, stale or
    replayed.  Verification failure is always total, never partial."""


def canonical_bytes(body: Mapping) -> bytes:
    return SIGNATURE_DOMAIN + json.dumps(
        body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


def _now(now: Optional[datetime.datetime]) -> datetime.datetime:
    return now or datetime.datetime.now(datetime.timezone.utc)


def _parse_timestamp(value: object, label: str) -> datetime.datetime:
    if not isinstance(value, str):
        raise ObservationError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ObservationError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def observation_summary(result, *, operator_group: Optional[str],
                        index_lag: Optional[int],
                        challenge_hashes: Optional[Mapping[int, str]] = None,
                        ) -> dict:
    """Sanitized public view of one endpoint observation.

    Only information a public directory could already carry.  Resolved
    addresses are deliberately reduced to the address families seen, not
    the addresses themselves: publishing an operator's IPs is telemetry
    they did not volunteer, and adds nothing a verifier needs.
    """
    endpoint: EndpointId = result.endpoint
    summary = {
        "endpoint": str(endpoint),
        "operatorGroup": operator_group,
        "reachable": bool(result.reachable),
        "tlsValid": result.tls_valid,
        "tlsFingerprint": result.tls_fingerprint,
        "serverVersion": result.server_version,
        "protocolVersion": result.protocol_version,
        "coreHeight": result.core_height,
        "electrumHeight": result.height,
        "indexLag": index_lag,
        "addressFamilies": sorted(
            family for family, seen in (
                ("ipv4", bool(result.resolved_ipv4)),
                ("ipv6", bool(result.resolved_ipv6))) if seen),
    }
    backend = result.backend or {}
    core = backend.get("backend") or {}
    identity = core.get("identity") or {}
    summary["backendClaim"] = {
        "coreVersion": core.get("version"),
        "coreBlocks": core.get("blocks"),
        "repository": identity.get("sourceRepository"),
        "commit": identity.get("sourceCommit"),
    }
    if challenge_hashes:
        summary["challengeHashes"] = {
            str(height): value for height, value in sorted(challenge_hashes.items())}
    if result.asset_methods is not None:
        summary["assetCapability"] = dict(sorted(result.asset_methods.items()))
    return summary


def build_observation_bundle(*, observer_id: str, observer_key_id: str,
                             sequence: int, crawl_id: str,
                             challenge_nonce: str,
                             challenge_heights: List[int],
                             observations: List[dict],
                             generated_at: Optional[datetime.datetime] = None,
                             valid_for_minutes: int = 60) -> dict:
    """Assemble an unsigned bundle body.  Deterministic callers pass
    ``generated_at``; production callers let it default to now."""
    if not isinstance(observer_id, str) or not observer_id:
        raise ObservationError("observerId must be a non-empty string")
    if not isinstance(sequence, int) or isinstance(sequence, bool) \
            or sequence < 1:
        raise ObservationError("sequence must be a positive integer")
    now = _now(generated_at).replace(microsecond=0)
    expires = now + datetime.timedelta(minutes=max(1, valid_for_minutes))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "observerId": observer_id,
        "observerKeyId": observer_key_id,
        "sequence": sequence,
        "crawlId": crawl_id,
        "challengeNonce": challenge_nonce,
        "challengeHeights": sorted(int(h) for h in challenge_heights),
        "generatedAt": now.isoformat(),
        "expiresAt": expires.isoformat(),
        "observations": observations,
    }


def sign_observation_bundle(body: Mapping, private_key, *,
                            key_id: str) -> dict:
    """Sign a bundle body with the observer's Ed25519 private key."""
    signature = private_key.sign(canonical_bytes(body))
    return {
        "observation": dict(body),
        "signature": {
            "algorithm": "ed25519",
            "keyId": key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def verify_observation_bundle(document: Mapping, trusted_keys: Dict[str, bytes],
                              *, observer_sequence_high_water: Mapping[str, int],
                              now: Optional[datetime.datetime] = None,
                              max_clock_skew_seconds: int =
                              DEFAULT_MAX_CLOCK_SKEW_SECONDS,
                              max_age_seconds: int =
                              DEFAULT_MAX_BUNDLE_AGE_SECONDS) -> dict:
    """Verify a signed observation bundle and return its body.

    Fail-closed checks, all of them total:

    * document shape, algorithm and base64 signature;
    * the signing key id must be in the *configured* trust registry
      (signing your own key proves nothing);
    * the Ed25519 signature over the canonical body;
    * exact schema version: a future version is refused, not guessed;
    * time semantics, deliberately one-sided: ``generatedAt`` may sit at
      most ``max_clock_ske_seconds`` in the future (clock skew bounds
      FUTURE timestamps, so a fast attacker clock cannot mint fresh
      bundles), while age is bounded by ``expiresAt`` and by
      ``max_age_seconds``: an observation that is still valid and under
      the hard maximum age is current, not stale;
    * sequence must exceed the per-observer high-water mark, so a
      replayed or rolled-back bundle is refused however valid its
      signature is.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(document, Mapping):
        raise ObservationError("bundle document must be an object")
    body = document.get("observation")
    signature = document.get("signature")
    if not isinstance(body, Mapping) or not isinstance(signature, Mapping):
        raise ObservationError("bundle must contain observation and signature")
    if signature.get("algorithm") != "ed25519":
        raise ObservationError("unsupported signature algorithm")
    key_id = signature.get("keyId")
    if key_id not in trusted_keys:
        raise ObservationError(f"bundle signed by untrusted observer key {key_id!r}")
    try:
        raw = base64.b64decode(signature.get("value", ""), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ObservationError("signature is not valid base64") from exc
    try:
        Ed25519PublicKey.from_public_bytes(trusted_keys[key_id]).verify(
            raw, canonical_bytes(body))
    except InvalidSignature as exc:
        raise ObservationError("bundle signature does not verify") from exc

    if body.get("schemaVersion") != SCHEMA_VERSION:
        raise ObservationError(
            f"unsupported observation schemaVersion {body.get('schemaVersion')!r}")
    observer_id = body.get("observerId")
    if not isinstance(observer_id, str) or not observer_id:
        raise ObservationError("observerId must be a non-empty string")
    if body.get("observerKeyId") != key_id:
        raise ObservationError(
            "signature key id does not match the bundle observerKeyId")

    sequence = body.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) \
            or sequence < 1:
        raise ObservationError("sequence must be a positive integer")
    high_water = int(observer_sequence_high_water.get(key_id, 0))
    if sequence <= high_water:
        raise ObservationError(
            f"observer sequence {sequence} is not above the accepted "
            f"high-water mark {high_water}; refusing a replay or rollback")

    current = _now(now)
    generated = _parse_timestamp(body.get("generatedAt"), "generatedAt")
    expires = _parse_timestamp(body.get("expiresAt"), "expiresAt")
    if expires <= generated:
        raise ObservationError("expiresAt must be after generatedAt")
    if current > expires:
        raise ObservationError("observation bundle has expired")
    skew = datetime.timedelta(seconds=max_clock_skew_seconds)
    # Skew bounds FUTURE timestamps only: an honest observer whose clock
    # runs slightly fast must not be able to mint bundles from "later
    # than now", but a verifier checking a few minutes after signing (or
    # with mild clock drift of its own) must not refuse a fresh,
    # unexpired bundle.  Age is bounded by expiresAt above and by the
    # hard maximum age below; replay is bounded by the mandatory
    # sequence high-water check, which no timestamp resets.
    if generated - current > skew:
        raise ObservationError(
            "generatedAt is further in the future than the tolerated skew")
    if current - generated > datetime.timedelta(seconds=max_age_seconds):
        raise ObservationError("observation bundle is too old to be current")

    observations = body.get("observations")
    if not isinstance(observations, list) or len(observations) > 4096:
        raise ObservationError("observations must be a bounded list")
    for item in observations:
        if not isinstance(item, Mapping) or "endpoint" not in item:
            raise ObservationError("each observation needs an endpoint")
    heights = body.get("challengeHeights")
    if not isinstance(heights, list) or len(heights) > 64 \
            or any(not isinstance(h, int) or isinstance(h, bool) or h < 0
                   for h in heights):
        raise ObservationError("challengeHeights must be a bounded int list")
    return dict(body)


def generate_observer_keypair(output_dir, *, name: str = "observer"):
    """Create a new Ed25519 observer keypair on the local machine.

    Writes ``<name>-private.hex`` (0600, the raw 32-byte seed) and
    ``<name>-public.hex``.  The private key is never printed, logged or
    returned; only the public key and its key id are.
    """
    import hashlib
    import pathlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    directory = pathlib.Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    private_path = directory / f"{name}-private.hex"
    public_path = directory / f"{name}-public.hex"
    if private_path.exists() or public_path.exists():
        raise ObservationError(
            f"refusing to overwrite existing key material in {directory}")
    private_key = Ed25519PrivateKey.generate()
    seed = private_key.private_bytes_raw()
    public_bytes = private_key.public_key().public_bytes_raw()
    private_path.write_bytes(seed.hex().encode("ascii") + b"\n")
    private_path.chmod(0o600)
    public_path.write_bytes(public_bytes.hex().encode("ascii") + b"\n")
    return {
        "publicKeyHex": public_bytes.hex(),
        "keyId": hashlib.sha256(public_bytes).hexdigest()[:16],
        "publicKeyPath": str(public_path),
        "privateKeyPath": str(private_path),
    }


def load_observer_private_key(path) -> tuple:
    """Load an observer private key from hex.  Returns (key, public_hex)."""
    import pathlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = pathlib.Path(path).read_text(encoding="utf-8").strip()
    seed = bytes.fromhex(raw)
    if len(seed) != 32:
        raise ObservationError("observer private key must be 32 bytes of hex")
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    return private_key, private_key.public_key().public_bytes_raw().hex()
