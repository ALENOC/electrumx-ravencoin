# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

import json
import os
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import policy as core_policy  # noqa: E402
import update_policy  # noqa: E402


COMMIT = "c" * 40
REPORT = "d" * 64


def _write_key(tmp_path):
    private_key, public_bytes = core_policy.generate_keypair()
    key_path = tmp_path / "policy-key.hex"
    key_path.write_text(public_bytes.hex() + "\n", encoding="ascii")
    return private_key, public_bytes, key_path


def _signed_policy(private_key, public_bytes, *, version, commit=COMMIT,
                   report_digest=REPORT, status="KNOWN_SAFE", marker=None):
    release = {
        "repository": "RavenProject/Ravencoin",
        "tag": "v4.8.0",
        "version": "4.8.0",
        "commit": commit,
        "status": status,
    }
    if status == "KNOWN_SAFE":
        release["certification"] = {"result": "PASS"}
        release["reportDigest"] = report_digest
    elif status == "REVOKED":
        release["revocationReason"] = "test revocation"
    body = core_policy.build_policy(
        policy_version=version,
        safety_profile="test-profile",
        releases=[release],
        valid_for_days=30,
    )
    if marker is not None:
        # Unknown top-level fields are signed. This is useful for the
        # same-version equivocation regression without weakening validation.
        body["testMarker"] = marker
    key_id = core_policy.key_id_for(public_bytes)
    return core_policy.sign_policy(body, private_key, key_id=key_id)


def _write_json(path, document):
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def test_verified_remote_higher_policy_is_selected_and_cached(tmp_path):
    private_key, public_bytes, key_path = _write_key(tmp_path)
    bundled = tmp_path / "bundled.json"
    cache = tmp_path / "cache.json"
    _write_json(bundled, _signed_policy(private_key, public_bytes, version=2))
    remote = _signed_policy(private_key, public_bytes, version=3)

    resolved = update_policy.resolve_safe_core_policy(
        bundled_path=str(bundled), cache_path=str(cache), key_path=str(key_path),
        minimum_policy_version=0,
        fetcher=lambda url: json.dumps(remote).encode("utf-8"),
    )

    assert resolved.version == 3
    assert resolved.source == "remote"
    assert resolved.commits == frozenset({COMMIT})
    assert resolved.certification_digests == {COMMIT: REPORT}
    cached = json.loads(cache.read_text(encoding="utf-8"))
    assert cached["policy"]["policyVersion"] == 3
    if os.name == "posix":
        assert cache.stat().st_mode & 0o777 == 0o600


def test_invalid_remote_never_replaces_verified_cache(tmp_path):
    private_key, public_bytes, key_path = _write_key(tmp_path)
    bundled = tmp_path / "bundled.json"
    cache = tmp_path / "cache.json"
    _write_json(bundled, _signed_policy(private_key, public_bytes, version=2))
    cached_document = _signed_policy(private_key, public_bytes, version=3)
    _write_json(cache, cached_document)
    before = cache.read_bytes()

    forged = _signed_policy(private_key, public_bytes, version=4)
    forged["signature"]["value"] = "AAAA"
    resolved = update_policy.resolve_safe_core_policy(
        bundled_path=str(bundled), cache_path=str(cache), key_path=str(key_path),
        minimum_policy_version=3,
        fetcher=lambda url: json.dumps(forged).encode("utf-8"),
    )

    assert resolved.version == 3
    assert resolved.source == "cache"
    assert cache.read_bytes() == before


def test_network_outage_uses_verified_cache_at_floor(tmp_path):
    private_key, public_bytes, key_path = _write_key(tmp_path)
    bundled = tmp_path / "bundled.json"
    cache = tmp_path / "cache.json"
    _write_json(bundled, _signed_policy(private_key, public_bytes, version=2))
    _write_json(cache, _signed_policy(private_key, public_bytes, version=3))

    def unavailable(_url):
        raise OSError("offline")

    resolved = update_policy.resolve_safe_core_policy(
        bundled_path=str(bundled), cache_path=str(cache), key_path=str(key_path),
        minimum_policy_version=3, fetcher=unavailable,
    )
    assert resolved.version == 3
    assert resolved.source == "cache"


def test_floor_blocks_rollback_when_only_older_policy_is_available(tmp_path):
    private_key, public_bytes, key_path = _write_key(tmp_path)
    bundled = tmp_path / "bundled.json"
    cache = tmp_path / "missing-cache.json"
    _write_json(bundled, _signed_policy(private_key, public_bytes, version=2))

    with pytest.raises(update_policy.PolicyResolutionError, match="version floor 3"):
        update_policy.resolve_safe_core_policy(
            bundled_path=str(bundled), cache_path=str(cache), key_path=str(key_path),
            minimum_policy_version=3, allow_remote=False,
        )


def test_older_remote_cannot_overwrite_newer_verified_cache(tmp_path):
    private_key, public_bytes, key_path = _write_key(tmp_path)
    bundled = tmp_path / "bundled.json"
    cache = tmp_path / "cache.json"
    _write_json(bundled, _signed_policy(private_key, public_bytes, version=2))
    _write_json(cache, _signed_policy(private_key, public_bytes, version=4))
    before = cache.read_bytes()
    remote = _signed_policy(private_key, public_bytes, version=3)

    resolved = update_policy.resolve_safe_core_policy(
        bundled_path=str(bundled), cache_path=str(cache), key_path=str(key_path),
        minimum_policy_version=3,
        fetcher=lambda url: json.dumps(remote).encode("utf-8"),
    )
    assert resolved.version == 4
    assert resolved.source == "cache"
    assert cache.read_bytes() == before


def test_same_version_signed_equivocation_fails_closed(tmp_path):
    private_key, public_bytes, key_path = _write_key(tmp_path)
    bundled = tmp_path / "bundled.json"
    cache = tmp_path / "cache.json"
    _write_json(
        bundled,
        _signed_policy(private_key, public_bytes, version=3, marker="A"),
    )
    _write_json(
        cache,
        _signed_policy(private_key, public_bytes, version=3, marker="B"),
    )

    with pytest.raises(update_policy.PolicyResolutionError, match="conflicting valid signed"):
        update_policy.resolve_safe_core_policy(
            bundled_path=str(bundled), cache_path=str(cache), key_path=str(key_path),
            minimum_policy_version=3, allow_remote=False,
        )


def test_revoked_ravenproject_identity_is_not_returned_as_trusted(tmp_path):
    private_key, public_bytes, key_path = _write_key(tmp_path)
    bundled = tmp_path / "bundled.json"
    cache = tmp_path / "cache.json"
    _write_json(
        bundled,
        _signed_policy(
            private_key, public_bytes, version=3, status="REVOKED"),
    )
    resolved = update_policy.resolve_safe_core_policy(
        bundled_path=str(bundled), cache_path=str(cache), key_path=str(key_path),
        minimum_policy_version=3, allow_remote=False,
    )
    assert resolved.commits == frozenset()
    assert resolved.certification_digests == {}


def test_malformed_report_digest_in_known_safe_policy_fails_closed(tmp_path):
    private_key, public_bytes, key_path = _write_key(tmp_path)
    bundled = tmp_path / "bundled.json"
    cache = tmp_path / "cache.json"
    _write_json(
        bundled,
        _signed_policy(
            private_key, public_bytes, version=3, report_digest="short"),
    )
    with pytest.raises(update_policy.PolicyResolutionError, match="malformed reportDigest"):
        update_policy.resolve_safe_core_policy(
            bundled_path=str(bundled), cache_path=str(cache), key_path=str(key_path),
            minimum_policy_version=3, allow_remote=False,
        )
