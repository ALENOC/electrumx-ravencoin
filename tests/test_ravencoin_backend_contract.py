# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Regression contract for the public ``server.ravencoin_backend`` RPC.

This RPC is a production API consumed by deployed RavenTag Android
clients.  The fields below, their JSON types and their semantics are a
compatibility contract: breaking any of them is a release-blocking
regression, regardless of what the Network Observer workstream adds.

The contract also holds directionally: the RPC describes LOCAL
ElectrumX to Ravencoin Core backend evidence only.  Network Observer
functionality (observers, quorum, registries, asset sampling) lives in
the monitor package, is never imported by the server, and can never be
required for this RPC to answer.
"""

import sys

import pytest

from electrumx.lib.coins import Ravencoin
from electrumx.server import ravencoin_backend as backend_module
from electrumx.server.ravencoin_backend import (
    BackendIdentity, RavencoinBackendStatus, evaluate_backend,
)

#: The exact response schema deployed RavenTag clients consume.  Key
#: order is irrelevant (JSON object); presence, type and semantics are
#: not.  Types are expressed as JSON types, not Python types.
RAVENTAG_CONTRACT = {
    "backend.version": str,
    "backend.versionNumber": int,
    "backend.network": str,
    "backend.identity.sourceRepository": str,
    "backend.identity.sourceCommit": str,
    "backend.identity.evidence": str,
    "compatibility.coreSafe": bool,
    "compatibility.networkMatches": bool,
    "compatibility.backendSynchronized": bool,
    "compatibility.kawpowHeightValidation": bool,
    "compatibility.checkpoint4487775": bool,
    "observedAt": int,
}


def _leaf(document, dotted):
    value = document
    for part in dotted.split("."):
        value = value[part]
    return value


def _build_status(**overrides) -> RavencoinBackendStatus:
    """A healthy mainnet status at the incident checkpoint height."""
    network_info = {
        "version": 4080000,
        "subversion": "/Ravencoin:4.8.0/",
    }
    blockchain_info = {
        "chain": "main",
        "blocks": Ravencoin.INCIDENT_CHECKPOINT_HEIGHT,
        "headers": Ravencoin.INCIDENT_CHECKPOINT_HEIGHT,
        "initialblockdownload": False,
    }
    return evaluate_backend(
        network_info, blockchain_info, "mainnet",
        checkpoint_hash=Ravencoin.INCIDENT_CHECKPOINT_HASH,
        observed_at=1760000000, **overrides)


def _public_dict() -> dict:
    identity = BackendIdentity.from_config(
        repository="RavenProject/Ravencoin",
        tag="v4.8.0",
        commit="22549129888d02e0e08fcdb9f96f3c699167e774",
        artifact_sha256="0" * 64,
        evidence=backend_module.IdentityEvidence.BUILD_VERIFIED,
    )
    return _build_status().public_dict("ElectrumX-RVN 1.13.10", identity)


def test_ravencoin_backend_method_remains_dispatched():
    """Requirement 1: the RPC still exists under its exact name."""
    from electrumx.server import session as session_module
    source = open(session_module.__file__, encoding="utf-8").read()
    assert "'server.ravencoin_backend': self.ravencoin_backend" in source, \
        "server.ravencoin_backend disappeared from the session dispatch map"


def test_ravencoin_backend_response_has_every_contract_field():
    """Requirement 2: all RavenTag-consumed fields exist."""
    document = _public_dict()
    missing = [key for key in RAVENTAG_CONTRACT
               if _leaf(document, key) is None and key != "backend.identity.sourceRepository"]
    for key in RAVENTAG_CONTRACT:
        value = _leaf(document, key)
        assert value is not None, f"contract field {key} is missing"


@pytest.mark.parametrize("dotted,expected", sorted(RAVENTAG_CONTRACT.items()))
def test_ravencoin_backend_field_types_unchanged(dotted, expected):
    """Requirement 3: JSON types remain what deployed clients parse."""
    value = _leaf(_public_dict(), dotted)
    if expected is bool:
        assert isinstance(value, bool)
    elif expected is int:
        assert isinstance(value, int) and not isinstance(value, bool)
    else:
        assert isinstance(value, expected)


def test_ravencoin_backend_semantics_unchanged():
    """Requirement 4: semantics stay local-evidence based.

    coreSafe is the AND of the local version/network/checkpoint checks;
    checkpoint4487775 is True only when a real local comparison at the
    checkpoint height succeeded; observedAt is a unix timestamp that
    moves with observation time.
    """
    healthy = _public_dict()
    assert healthy["compatibility"]["coreSafe"] is True
    assert healthy["compatibility"]["checkpoint4487775"] is True

    wrong_network = evaluate_backend(
        {"version": 4080000, "subversion": "/Ravencoin:4.8.0/"},
        {"chain": "test", "blocks": 100, "headers": 100,
         "initialblockdownload": True},
        "mainnet", checkpoint_hash=None, observed_at=1760000000)
    document = wrong_network.public_dict("ElectrumX-RVN 1.13.10")
    assert document["compatibility"]["networkMatches"] is False
    assert document["compatibility"]["coreSafe"] is False
    assert document["compatibility"]["checkpoint4487775"] is False
    assert document["compatibility"]["kawpowHeightValidation"] is True

    later = evaluate_backend(
        {"version": 4080000, "subversion": "/Ravencoin:4.8.0/"},
        {"chain": "main", "blocks": Ravencoin.INCIDENT_CHECKPOINT_HEIGHT,
         "headers": Ravencoin.INCIDENT_CHECKPOINT_HEIGHT,
         "initialblockdownload": False},
        "mainnet", checkpoint_hash=Ravencoin.INCIDENT_CHECKPOINT_HASH,
        observed_at=1760009999)
    assert later.public_dict("x")["observedAt"] == 1760009999


def test_rpc_has_no_dependency_on_monitor_or_observer_code():
    """Requirements 5 to 8, structurally: an Observer-unaware client and
    an Observer-less deployment get the same RPC, because the server
    session module never imports the monitor package.  Observer
    failure, a disabled monitor, or no external observers can only
    affect the monitor, never this RPC."""
    from electrumx.server import session as session_module
    source = open(session_module.__file__, encoding="utf-8").read()
    assert "import network_observer" not in source
    assert "from monitor" not in source
    # And building the response document loads no monitor code as a side
    # effect (other test modules in the same process may have imported
    # the monitor package for their own purposes; what matters is that
    # this RPC's call path does not).
    before = {name for name in sys.modules if name.startswith("monitor")}
    _public_dict()
    after = {name for name in sys.modules if name.startswith("monitor")}
    assert after == before


def test_public_dict_shape_is_stable_for_old_clients():
    """Old clients ignore unknown fields, so additive keys are allowed,
    but the top-level shape must stay three known objects plus a
    timestamp: no field the contract consumes may move or be renamed."""
    document = _public_dict()
    assert set(document) >= {"server", "serverVersion", "backend",
                             "compatibility", "observedAt"}
    assert document["server"] == "ElectrumX-RVN"
    assert isinstance(document["backend"]["identity"], dict)
    assert backend_module.MINIMUM_SAFE_CORE_STRING == \
        document["compatibility"]["minimumSafeCore"]
