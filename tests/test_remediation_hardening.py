# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression tests for the low-cost remediation items: malformed signature
key IDs fail closed (GLM53-RVN-020), multi-architecture manifests are
evaluated against the declared target set, and an out-of-range UTXO lookup
raises a controlled error instead of a reachable assertion
(GLM53-RVN-009)."""

import importlib.util
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import policy  # noqa: E402
import update_manifest as um  # noqa: E402
import update_decision as ud  # noqa: E402

KEY_PRIVATE, KEY_PUBLIC = policy.generate_keypair()
KEY_ID = policy.key_id_for(KEY_PUBLIC)
TRUSTED = {KEY_ID: KEY_PUBLIC}
CURRENT_ARTIFACT_DIGEST = "sha256:" + "b" * 64
CURRENT_PROVENANCE_DIGEST = "sha256:" + "c" * 64


def _policy_document(key_id):
    return {
        "policy": {"any": "body"},
        "signature": {"algorithm": policy.SIGNATURE_ALGORITHM,
                      "keyId": key_id, "value": "AAAA"},
    }


def _manifest_document(key_id):
    return {
        "manifest": {"any": "body"},
        "signature": {"algorithm": um.SIGNATURE_ALGORITHM,
                      "keyId": key_id, "value": "AAAA"},
    }


def test_policy_key_id_string_is_rejected_cleanly_when_unknown():
    try:
        policy.verify_policy(_policy_document("unknown"), TRUSTED)
        raised = None
    except policy.PolicyError as exc:
        raised = exc
    assert raised is not None and "unknown key id" in str(raised)


def test_policy_malformed_key_id_types_never_raise_type_error():
    for key_id in ([], {}, None, 123, "x" * 500):
        try:
            policy.verify_policy(_policy_document(key_id), TRUSTED)
            raised = None
        except policy.PolicyError as exc:
            raised = exc
        except TypeError:
            raised = None
            assert False, f"keyId {key_id!r} caused a TypeError"
        assert isinstance(raised, policy.PolicyError), \
            f"keyId {key_id!r} must fail closed as PolicyError"


def test_manifest_malformed_key_id_types_never_raise_type_error():
    for key_id in ([], {}, None, 123, "x" * 500):
        try:
            um.verify_manifest(_manifest_document(key_id), TRUSTED)
            raised = None
        except um.ManifestError:
            raised = True
        except TypeError:
            assert False, f"keyId {key_id!r} caused a TypeError"
        assert raised is True, f"keyId {key_id!r} must fail closed"


def test_policy_wrong_length_signature_is_policy_error():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import base64
    body = {"policyVersion": 3, "policyId": "x", "safetyProfile": "x",
            "validFrom": "2026-01-01T00:00:00Z", "validUntil": "2027-01-01T00:00:00Z",
            "releases": []}
    private = Ed25519PrivateKey.from_private_bytes(b"k" * 32)
    document = policy.sign_policy(body, private, key_id="0" * 16)
    document["signature"]["value"] = base64.b64encode(b"short").decode()
    try:
        policy.verify_policy(document, TRUSTED)
        raised = False
    except policy.PolicyError:
        raised = True
    assert raised


def _host(arch):
    return ud.HostFacts(
        architecture=arch,
        installed_updater_version="2.0.0",
        current_electrumx_version="1.13.3",
        current_core_commit="c" * 40,
        current_db_schema=1,
        current_artifact_revision=0,
        current_artifact_digest=CURRENT_ARTIFACT_DIGEST,
        current_provenance_digest=CURRENT_PROVENANCE_DIGEST,
    )


def _manifest(arch):
    return {
        "schemaVersion": 2,
        "electrumxVersion": "1.13.3",
        "artifact_revision": 0,
        "channel": "stable",
        "releaseTimestamp": "2026-08-22T00:00:00Z",
        "artifactDigest": "sha256:" + "a" * 64,
        "provenanceDigest": "sha256:" + "d" * 64,
        "architecture": arch,
        "coreVersion": "4.8.0",
        "coreRepository": "RavenProject/Ravencoin",
        "coreTag": "v4.8.0",
        "coreCommit": "c" * 40,
        "certificationReportDigest": "e" * 64,
        "safeCorePolicyVersion": 3,
        "requiredUpdaterVersion": "2.0.0",
        "configCompatibility": {},
        "dbCompatibility": {"schemaVersion": 1},
        "rollbackSafe": True,
        "consensusImpact": False,
        "autoUpdateEligible": True,
        "installerFilename": "electrumx-ravencoin-install.py",
        "installerDigest": "sha256:" + "f" * 64,
    }


def test_multi_architecture_manifest_accepted_for_declared_target():
    decision = ud.evaluate_verification(
        manifest=_manifest("linux/amd64,linux/arm64"),
        signature_valid=True,
        downloaded_artifact_digest="sha256:" + "a" * 64,
        host=_host("linux/amd64"),
        safe_core_certified_commits=frozenset(),
    )
    assert decision.verdict is not ud.VerificationVerdict.REFUSED_ARCHITECTURE_MISMATCH


def test_multi_architecture_manifest_refused_for_undeclared_target():
    decision = ud.evaluate_verification(
        manifest=_manifest("linux/amd64,linux/arm64"),
        signature_valid=True,
        downloaded_artifact_digest="sha256:" + "a" * 64,
        host=_host("linux/riscv64"),
        safe_core_certified_commits=frozenset(),
    )
    assert decision.verdict is ud.VerificationVerdict.REFUSED_ARCHITECTURE_MISMATCH


def test_non_string_architecture_is_refused():
    decision = ud.evaluate_verification(
        manifest=_manifest(["linux/amd64"]),
        signature_valid=True,
        downloaded_artifact_digest="sha256:" + "a" * 64,
        host=_host("linux/amd64"),
        safe_core_certified_commits=frozenset(),
    )
    assert decision.verdict is ud.VerificationVerdict.REFUSED_ARCHITECTURE_MISMATCH


def test_spend_utxo_short_hash_read_raises_chainerror_not_assert():
    """GLM53-RVN-009: a UTXO whose tx_num reads past the hashes file must
    raise a controlled ChainError, never a reachable assert."""
    spec = importlib.util.spec_from_file_location(
        "bp_for_test", ROOT / "electrumx" / "server" / "block_processor.py")
    bp = importlib.util.module_from_spec(spec)
    sys.modules["bp_for_test"] = bp
    spec.loader.exec_module(bp)

    from electrumx.lib.hash import HASHX_LEN

    tx_hash = b"\x11" * 32

    class Iterator:

        def __init__(self, items):
            self.items = items

        def __iter__(self):
            return iter(self.items)

    class FakeDB:
        utxo_db = None

        def fs_tx_hash(self, tx_num):
            return None, None

    FakeDB.utxo_db = SimpleNamespace(
        iterator=lambda prefix: Iterator([]))

    processor = bp.BlockProcessor.__new__(bp.BlockProcessor)
    processor.utxo_cache = {}
    processor.utxo_deletes = []
    processor.db = FakeDB()

    # No candidates: the pre-existing not-found ChainError path.
    try:
        processor.spend_utxo(tx_hash, 0)
        raised = None
    except bp.ChainError:
        raised = "not-found"

    # Two candidates with the same compressed prefix (len > 1) whose fs read
    # is short: the new controlled error, not AssertionError.

    class TwoIterator:

        def __iter__(self):
            key1 = b"h" + tx_hash[:4] + (0).to_bytes(4, "little") + \
                (5).to_bytes(5, "little")
            key2 = b"h" + tx_hash[:4] + (0).to_bytes(4, "little") + \
                (6).to_bytes(5, "little")
            value = b"h" * HASHX_LEN + b"a" * 4
            return iter([(key1, value), (key2, value)])
    FakeDB.utxo_db = SimpleNamespace(iterator=lambda prefix: TwoIterator())
    try:
        processor.spend_utxo(tx_hash, 0)
        raised = None
    except bp.ChainError as exc:
        raised = exc
    except AssertionError:
        raised = None
        assert False, "reachable assert must not fire for short hash reads"
    assert isinstance(raised, bp.ChainError) and "reindex required" in str(raised)
