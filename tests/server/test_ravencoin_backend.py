import pytest

from electrumx.lib.hash import hex_str_to_hash
from electrumx.server.ravencoin_backend import (
    INCIDENT_CHECKPOINT_HASH,
    MINIMUM_SAFE_CORE,
    RavencoinDatabaseMismatchError,
    UnsafeRavencoinCoreError,
    enforce_backend_policy,
    evaluate_backend,
    parse_core_version,
    verify_database_chain,
)


def network_info(version=4_080_000, subversion="/Ravencoin:4.8.0/"):
    return {"version": version, "subversion": subversion}


def blockchain_info(chain="main", blocks=4_494_000, headers=4_494_000, ibd=False):
    return {
        "chain": chain,
        "blocks": blocks,
        "headers": headers,
        "initialblockdownload": ibd,
    }


@pytest.mark.parametrize("version", [
    pytest.param(4_060_000, id="4.6.0"),
    pytest.param(4_060_100, id="4.6.1"),
    pytest.param(4_060_101, id="4.6.1.1"),
    pytest.param(4_070_000, id="4.7.0"),
])
def test_known_unsafe_core_versions_are_rejected(version):
    status = evaluate_backend(
        network_info(version), blockchain_info(), "mainnet",
        INCIDENT_CHECKPOINT_HASH, observed_at=100,
    )
    assert not status.version_safe
    with pytest.raises(UnsafeRavencoinCoreError):
        enforce_backend_policy(status)


@pytest.mark.parametrize("version, parsed", [
    (4_080_000, (4, 8, 0, 0)),
    (4_080_100, (4, 8, 1, 0)),
    (4_100_000, (4, 10, 0, 0)),
    (5_000_000, (5, 0, 0, 0)),
])
def test_safe_and_future_structural_versions_are_accepted(version, parsed):
    assert parse_core_version(version) == parsed
    assert parsed >= MINIMUM_SAFE_CORE
    status = evaluate_backend(
        network_info(version), blockchain_info(), "mainnet",
        INCIDENT_CHECKPOINT_HASH, observed_at=100,
    )
    assert status.core_safe
    assert enforce_backend_policy(status) is None


@pytest.mark.parametrize("version", [None, "4.8.0", True, -1])
def test_malformed_versions_fail_closed(version):
    with pytest.raises(ValueError):
        evaluate_backend(
            network_info(version), blockchain_info(), "mainnet",
            INCIDENT_CHECKPOINT_HASH,
        )


def test_wrong_network_and_checkpoint_are_rejected():
    wrong_network = evaluate_backend(
        network_info(), blockchain_info(chain="test"), "mainnet", None,
    )
    assert not wrong_network.network_matches
    with pytest.raises(UnsafeRavencoinCoreError):
        enforce_backend_policy(wrong_network)

    wrong_checkpoint = evaluate_backend(
        network_info(), blockchain_info(), "mainnet", "00" * 32,
    )
    assert not wrong_checkpoint.checkpoint_known
    with pytest.raises(UnsafeRavencoinCoreError):
        enforce_backend_policy(wrong_checkpoint)


def test_null_ibd_is_unknown_but_equal_heights_are_synchronized():
    status = evaluate_backend(
        network_info(), blockchain_info(ibd=None), "mainnet",
        INCIDENT_CHECKPOINT_HASH, observed_at=123,
    )
    assert status.initial_block_download is None
    assert status.synchronized
    assert status.public_dict("ElectrumX-RVN 1.13.0.dev1")["observedAt"] == 123


def test_explicit_unsafe_override_is_prominent_not_silent():
    status = evaluate_backend(
        network_info(4_070_000), blockchain_info(), "mainnet",
        INCIDENT_CHECKPOINT_HASH,
    )
    warning = enforce_backend_policy(status, allow_unsafe=True)
    assert "unsafe Ravencoin backend" in warning
    assert "below 4.8.0" in warning


class FakeDaemon:
    def __init__(self, core_hash):
        self.core_hash = core_hash

    async def block_hex_hashes(self, height, count):
        assert count == 1
        return [self.core_hash]


class FakeDB:
    class State:
        height = 10
        tip = hex_str_to_hash("11" * 32)

    state = State()


@pytest.mark.asyncio
async def test_database_tip_mismatch_refuses_to_serve():
    with pytest.raises(RavencoinDatabaseMismatchError, match="rewind or rebuild"):
        await verify_database_chain(FakeDB(), FakeDaemon("22" * 32))


@pytest.mark.asyncio
async def test_unopened_database_is_refused_instead_of_crashing():
    class UnopenedDB:
        state = None

    with pytest.raises(RavencoinDatabaseMismatchError, match="state is unavailable"):
        await verify_database_chain(UnopenedDB(), FakeDaemon("11" * 32))


@pytest.mark.asyncio
async def test_block_processor_verifies_chain_only_after_opening_database(monkeypatch):
    """The chain check reads on-disk state, so it must follow the database open."""
    from types import SimpleNamespace

    from electrumx.server import block_processor

    calls = []

    class StubState:
        height = -1
        tip = hex_str_to_hash("00" * 32)

        def copy(self):
            return self

    class StubDB:
        def __init__(self):
            self.state = None

        async def open_for_sync(self):
            calls.append("open_for_sync")
            self.state = StubState()
            return self.state

    class StubOnDiskBlock:
        state = None

        @classmethod
        async def scan_files(cls):
            calls.append("scan_files")

    async def stub_verify(db, daemon):
        assert db.state is not None, "verification ran before the database was open"
        calls.append("verify_database_chain")

    class StopTest(Exception):
        pass

    async def stub_next_block_hashes():
        raise StopTest

    monkeypatch.setattr(block_processor, "verify_database_chain", stub_verify)
    monkeypatch.setattr(block_processor, "OnDiskBlock", StubOnDiskBlock)

    processor = SimpleNamespace(
        env=SimpleNamespace(write_bad_vouts_to_file=False),
        bad_vouts_path="/nonexistent",
        db=StubDB(),
        daemon=object(),
        state=None,
        next_block_hashes=stub_next_block_hashes,
    )

    with pytest.raises(StopTest):
        await block_processor.BlockProcessor.fetch_and_process_blocks(
            processor, None, None
        )

    assert calls == ["open_for_sync", "verify_database_chain", "scan_files"]


def test_abnormal_startup_failure_exits_non_zero():
    """Container and systemd restart policies need a non-zero exit on crash."""
    import pathlib
    import subprocess
    import sys

    script = pathlib.Path(__file__).resolve().parents[2] / "electrumx_server"
    completed = subprocess.run(
        [sys.executable, str(script)],
        env={"PATH": "/usr/bin:/bin", "COIN": "Ravencoin"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode != 0
    assert "terminated abnormally" in completed.stdout + completed.stderr


def test_backend_below_checkpoint_does_not_claim_verification():
    """A syncing backend must not publish checkpoint evidence it cannot have."""
    status = evaluate_backend(
        network_info(), blockchain_info(blocks=0, headers=457_998), "mainnet",
        checkpoint_hash=None, observed_at=100,
    )
    published = status.public_dict("ElectrumX-RVN 1.13.0.dev1")
    assert status.checkpoint_known          # cannot violate what it has not reached
    assert not status.checkpoint_verified
    assert published["compatibility"]["checkpoint4487775"] is False
    assert published["compatibility"]["backendSynchronized"] is False
    enforce_backend_policy(status)          # startup is still allowed while syncing


def test_backend_at_checkpoint_publishes_verified_checkpoint():
    status = evaluate_backend(
        network_info(), blockchain_info(), "mainnet",
        INCIDENT_CHECKPOINT_HASH, observed_at=100,
    )
    published = status.public_dict("ElectrumX-RVN 1.13.0.dev1")
    assert status.checkpoint_verified
    assert published["compatibility"]["checkpoint4487775"] is True


def test_wrong_checkpoint_above_height_is_unsafe():
    status = evaluate_backend(
        network_info(), blockchain_info(), "mainnet",
        "00" * 32, observed_at=100,
    )
    assert not status.checkpoint_known
    assert not status.checkpoint_verified
    assert status.public_dict("v")["compatibility"]["checkpoint4487775"] is False
    with pytest.raises(UnsafeRavencoinCoreError, match="checkpoint"):
        enforce_backend_policy(status)


# ---------------------------------------------------------- backend identity
from electrumx.server.ravencoin_backend import (  # noqa: E402
    BackendIdentity, IdentityEvidence, SAFETY_PROFILE,
)

CERTIFIED_COMMIT = "b60f50e04f1fba425b28804e61be2694faaf3469"
CERTIFIED_ARTIFACT = (
    "966cf8978af1f2e3f36e9733d011eb92f4116750af6f8e77c5a5ced525577c4c"
)


def test_identity_defaults_to_version_only():
    identity = BackendIdentity.from_config()
    assert identity.evidence == IdentityEvidence.VERSION_ONLY
    assert identity.public_dict() == {"evidence": "VERSION_ONLY"}


def test_identity_without_commit_cannot_be_attested():
    identity = BackendIdentity.from_config(repository="2miners/Ravencoin",
                                           evidence="BUILD_IDENTITY_ATTESTED")
    assert identity.evidence == IdentityEvidence.VERSION_ONLY
    assert "sourceRepository" not in identity.public_dict()


def test_operator_configured_identity_is_only_attested():
    identity = BackendIdentity.from_config(repository="RavenProject/Ravencoin",
                                           tag="v4.8.0", commit=CERTIFIED_COMMIT)
    assert identity.evidence == IdentityEvidence.ATTESTED
    assert identity.public_dict()["sourceCommit"] == CERTIFIED_COMMIT


def test_build_verified_requires_the_pinned_artifact_digest():
    with pytest.raises(ValueError, match="requires the pinned artifact digest"):
        BackendIdentity.from_config(repository="2miners/Ravencoin", tag="v4.8.0",
                                    commit=CERTIFIED_COMMIT,
                                    evidence="BUILD_IDENTITY_VERIFIED")


def test_build_verified_identity_is_published_in_full():
    identity = BackendIdentity.from_config(
        repository="2miners/Ravencoin", tag="v4.8.0", commit=CERTIFIED_COMMIT,
        artifact_sha256=CERTIFIED_ARTIFACT, evidence="BUILD_IDENTITY_VERIFIED")
    published = identity.public_dict()
    assert published["evidence"] == "BUILD_IDENTITY_VERIFIED"
    assert published["artifactSha256"] == CERTIFIED_ARTIFACT


@pytest.mark.parametrize("kwargs, message", [
    ({"repository": "attacker/Ravencoin", "commit": CERTIFIED_COMMIT}, "not one of"),
    ({"repository": "2miners/Ravencoin", "commit": "short"}, "commit is malformed"),
    ({"repository": "2miners/Ravencoin", "commit": CERTIFIED_COMMIT,
      "artifact_sha256": "nothex"}, "artifact digest is malformed"),
    ({"repository": "2miners/Ravencoin", "commit": CERTIFIED_COMMIT,
      "evidence": "TOTALLY_PROVEN"}, "unknown identity evidence"),
])
def test_malformed_identity_configuration_is_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        BackendIdentity.from_config(**kwargs)


def test_published_evidence_and_profile_travel_together():
    status = evaluate_backend(network_info(), blockchain_info(), "mainnet",
                             INCIDENT_CHECKPOINT_HASH, observed_at=100)
    identity = BackendIdentity.from_config(
        repository="2miners/Ravencoin", tag="v4.8.0", commit=CERTIFIED_COMMIT,
        artifact_sha256=CERTIFIED_ARTIFACT, evidence="BUILD_IDENTITY_VERIFIED")
    published = status.public_dict("ElectrumX-RVN 1.13.0.dev1", identity)
    assert published["compatibility"]["safetyProfile"] == SAFETY_PROFILE
    assert published["compatibility"]["identityEvidence"] == "BUILD_IDENTITY_VERIFIED"
    assert published["backend"]["identity"]["sourceCommit"] == CERTIFIED_COMMIT


def test_identity_is_optional_for_older_callers():
    status = evaluate_backend(network_info(), blockchain_info(), "mainnet",
                             INCIDENT_CHECKPOINT_HASH, observed_at=100)
    published = status.public_dict("ElectrumX-RVN 1.13.0.dev1")
    assert published["backend"]["identity"] == {"evidence": "VERSION_ONLY"}
