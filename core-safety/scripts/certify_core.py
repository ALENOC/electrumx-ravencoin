#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Run a safety profile against one exact Ravencoin Core release candidate.

Nothing here approves a candidate because its source contains a promising
string.  Each test either observes behaviour or reports that it could not, and a
test that could not run keeps the candidate out of the safe policy.

Scopes, so the report cannot overclaim:

``core``
    observes the candidate binaries or its own test suite.
``harness``
    verifies this project's enforcement rule and fixture integrity.  Necessary
    for the pipeline to be trustworthy, but not evidence about Core itself.
``provenance``
    checks release metadata and artifact digests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from candidate import (  # noqa: E402
    Candidate, CandidateState, TestResult, aggregate_state, load_profile,
    required_test_ids,
)

MAINNET_GENESIS = "0000006b444bc2f2ffe627be9d9e7e7a0730000870ef6eb6da46c8eae389df90"
CHECKPOINT_HEIGHT = 4_487_775
CHECKPOINT_HASH = "000000000002d64509e06e76ddbbe418c725291687ec62b41ecfc40386a091fd"
ENFORCEMENT_HEIGHT = 4_487_776
DECLARED_HEIGHT_OFFSET = 76
KAWPOW_HEADER_SIZE = 120

REGISTRY: dict = {}


class Outcome:
    """One test result plus the evidence that produced it."""

    def __init__(self, result: TestResult, detail: str, evidence: Optional[dict] = None):
        self.result = result
        self.detail = detail
        self.evidence = evidence or {}

    def to_dict(self) -> dict:
        return {"result": self.result.value, "detail": self.detail,
                "evidence": self.evidence}


def test(test_id: str, scope: str) -> Callable:
    def decorator(function):
        REGISTRY[test_id] = (function, scope)
        return function
    return decorator


class Environment:
    """Everything a test may be given.  Absent inputs cause UNAVAILABLE."""

    def __init__(self, *, candidate: Candidate, source_dir: Optional[pathlib.Path] = None,
                 bin_dir: Optional[pathlib.Path] = None,
                 mainnet_datadir: Optional[pathlib.Path] = None,
                 artifact: Optional[pathlib.Path] = None,
                 fixtures: Optional[dict] = None,
                 validator: Optional[Callable] = None,
                 rpc_timeout: int = 120):
        self.candidate = candidate
        self.source_dir = source_dir
        self.bin_dir = bin_dir
        self.mainnet_datadir = mainnet_datadir
        self.artifact = artifact
        self.fixtures = fixtures
        # The header validator under test.  Injectable so the suite can be run
        # against a deliberately broken implementation: a test that cannot fail
        # is not evidence of anything.
        self.validator = validator
        self.rpc_timeout = rpc_timeout

    # ---------------------------------------------------------------- helpers
    def binary(self, name: str) -> Optional[pathlib.Path]:
        if self.bin_dir is None:
            return None
        path = self.bin_dir / name
        return path if path.exists() else None

    def run(self, argv, timeout=None) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, check=False,
                              timeout=timeout or self.rpc_timeout)


def _cli(environment: Environment, datadir: pathlib.Path, *args) -> subprocess.CompletedProcess:
    raven_cli = environment.binary("raven-cli")
    return environment.run([str(raven_cli), f"-datadir={datadir}", *args])


def _start_node(environment: Environment, datadir: pathlib.Path, *extra) -> Optional[subprocess.Popen]:
    ravend = environment.binary("ravend")
    if ravend is None:
        return None
    datadir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [str(ravend), f"-datadir={datadir}", "-listen=0", "-connect=0", "-dnsseed=0",
         "-server=1", "-rpcuser=certify", "-rpcpassword=certify", "-printtoconsole=0",
         *extra],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + environment.rpc_timeout
    while time.time() < deadline:
        completed = _cli(environment, datadir, "-rpcuser=certify", "-rpcpassword=certify",
                         "getblockchaininfo")
        if completed.returncode == 0:
            return process
        if process.poll() is not None:
            return None
        time.sleep(2)
    _stop_node(environment, datadir, process)
    return None


def _stop_node(environment: Environment, datadir: pathlib.Path, process) -> None:
    if process is None:
        return
    _cli(environment, datadir, "-rpcuser=certify", "-rpcpassword=certify", "stop")
    try:
        process.wait(timeout=180)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()


# ------------------------------------------------------------------ provenance
@test("provenance-tag-resolves-to-commit", "provenance")
def _tag_resolves(environment: Environment) -> Outcome:
    candidate = environment.candidate
    if not candidate.commit_verified:
        return Outcome(TestResult.UNAVAILABLE,
                       "the discovery stage did not confirm that the tag resolves to "
                       "this commit")
    evidence = {"repository": candidate.repository, "tag": candidate.tag,
                "tagObject": candidate.tag_object, "commit": candidate.commit,
                "tagSignatureVerified": candidate.tag_verified}
    return Outcome(TestResult.PASS, "tag resolves to the candidate commit", evidence)


@test("provenance-artifact-digest", "provenance")
def _artifact_digest(environment: Environment) -> Outcome:
    candidate = environment.candidate
    if candidate.artifact_sha256 is None:
        return Outcome(TestResult.UNAVAILABLE, "no artifact digest is pinned")
    if environment.artifact is None or not environment.artifact.exists():
        return Outcome(TestResult.UNAVAILABLE, "the pinned artifact was not supplied")
    digest = hashlib.sha256(environment.artifact.read_bytes()).hexdigest()
    if digest != candidate.artifact_sha256:
        return Outcome(TestResult.FAIL,
                       "artifact digest does not match the pinned value",
                       {"expected": candidate.artifact_sha256, "observed": digest})
    return Outcome(TestResult.PASS, "artifact digest matches", {"sha256": digest})


# ----------------------------------------------------------------------- build
@test("build-candidate-commit", "core")
def _build(environment: Environment) -> Outcome:
    ravend = environment.binary("ravend")
    raven_cli = environment.binary("raven-cli")
    if ravend is None or raven_cli is None:
        return Outcome(TestResult.UNAVAILABLE,
                       "candidate binaries were not supplied, so the build could not "
                       "be observed")
    completed = environment.run([str(ravend), "--version"])
    if completed.returncode != 0:
        return Outcome(TestResult.FAIL, "ravend --version failed")
    first_line = completed.stdout.splitlines()[0] if completed.stdout else ""
    if environment.candidate.version not in first_line:
        return Outcome(TestResult.FAIL,
                       "the built binary does not report the candidate version",
                       {"expected": environment.candidate.version,
                        "observed": first_line})
    return Outcome(TestResult.PASS, "candidate binaries run and self-report the "
                                    "candidate version", {"versionLine": first_line})


# ------------------------------------------------------------------ behavioural
@test("mainnet-genesis", "core")
def _genesis(environment: Environment) -> Outcome:
    if environment.binary("ravend") is None:
        return Outcome(TestResult.UNAVAILABLE, "candidate binaries were not supplied")
    with tempfile.TemporaryDirectory(prefix="certify-genesis-") as workdir:
        datadir = pathlib.Path(workdir) / "data"
        process = _start_node(environment, datadir)
        if process is None:
            return Outcome(TestResult.FAIL, "the candidate did not start on mainnet "
                                            "with networking disabled")
        try:
            completed = _cli(environment, datadir, "-rpcuser=certify",
                             "-rpcpassword=certify", "getblockhash", "0")
            observed = completed.stdout.strip()
        finally:
            _stop_node(environment, datadir, process)
    if observed != MAINNET_GENESIS:
        return Outcome(TestResult.FAIL, "mainnet genesis hash differs",
                       {"expected": MAINNET_GENESIS, "observed": observed})
    return Outcome(TestResult.PASS, "canonical mainnet genesis", {"genesis": observed})


@test("regtest-consensus-smoke", "core")
def _regtest_smoke(environment: Environment) -> Outcome:
    if environment.binary("ravend") is None:
        return Outcome(TestResult.UNAVAILABLE, "candidate binaries were not supplied")
    with tempfile.TemporaryDirectory(prefix="certify-regtest-") as workdir:
        datadir = pathlib.Path(workdir) / "data"
        process = _start_node(environment, datadir, "-regtest=1", "-txindex=1",
                              "-assetindex=1", "-rest=1")
        if process is None:
            return Outcome(TestResult.FAIL, "the candidate did not start on regtest")
        try:
            common = ["-regtest=1", "-rpcuser=certify", "-rpcpassword=certify"]
            _cli(environment, datadir, *common, "generate", "10")
            info = _cli(environment, datadir, *common, "getblockchaininfo")
            if info.returncode != 0:
                return Outcome(TestResult.FAIL, "getblockchaininfo failed on regtest")
            parsed = json.loads(info.stdout)
        finally:
            _stop_node(environment, datadir, process)
    if parsed.get("chain") != "regtest":
        return Outcome(TestResult.FAIL, "chain is not regtest", {"chain": parsed.get("chain")})
    if parsed.get("blocks") != parsed.get("headers") or parsed.get("blocks", 0) < 1:
        return Outcome(TestResult.FAIL, "blocks and headers did not advance together",
                       {"blocks": parsed.get("blocks"), "headers": parsed.get("headers")})
    return Outcome(TestResult.PASS, "regtest chain advanced coherently",
                   {"blocks": parsed.get("blocks")})


@test("regtest-asset-consensus", "core")
def _regtest_assets(environment: Environment) -> Outcome:
    if environment.binary("ravend") is None:
        return Outcome(TestResult.UNAVAILABLE, "candidate binaries were not supplied")
    asset_name = "CERTIFY.PROFILE.V1"
    with tempfile.TemporaryDirectory(prefix="certify-assets-") as workdir:
        datadir = pathlib.Path(workdir) / "data"
        process = _start_node(environment, datadir, "-regtest=1", "-assetindex=1",
                              "-txindex=1")
        if process is None:
            return Outcome(TestResult.FAIL, "the candidate did not start on regtest")
        try:
            common = ["-regtest=1", "-rpcuser=certify", "-rpcpassword=certify"]
            _cli(environment, datadir, *common, "generate", "500")
            issued = _cli(environment, datadir, *common, "issue", asset_name, "1000")
            if issued.returncode != 0:
                return Outcome(TestResult.FAIL, "asset issuance was rejected",
                               {"stderr": issued.stderr.strip()[:200]})
            _cli(environment, datadir, *common, "generate", "1")
            listed = _cli(environment, datadir, *common, "listassets", asset_name, "true")
            if listed.returncode != 0 or asset_name not in listed.stdout:
                return Outcome(TestResult.FAIL, "the issued asset was not indexed")
        finally:
            _stop_node(environment, datadir, process)
    return Outcome(TestResult.PASS, "asset consensus issued and indexed an asset",
                   {"asset": asset_name})


@test("required-indexes-usable", "core")
def _required_indexes(environment: Environment) -> Outcome:
    if environment.binary("ravend") is None:
        return Outcome(TestResult.UNAVAILABLE, "candidate binaries were not supplied")
    with tempfile.TemporaryDirectory(prefix="certify-indexes-") as workdir:
        datadir = pathlib.Path(workdir) / "data"
        process = _start_node(environment, datadir, "-regtest=1", "-txindex=1",
                              "-assetindex=1", "-rest=1")
        if process is None:
            return Outcome(TestResult.FAIL, "the candidate did not start on regtest")
        try:
            common = ["-regtest=1", "-rpcuser=certify", "-rpcpassword=certify"]
            _cli(environment, datadir, *common, "generate", "2")
            block_hash = _cli(environment, datadir, *common, "getblockhash", "1").stdout.strip()
            block = _cli(environment, datadir, *common, "getblock", block_hash)
            if block.returncode != 0:
                return Outcome(TestResult.FAIL, "getblock failed")
            coinbase = json.loads(block.stdout)["tx"][0]
            raw = _cli(environment, datadir, *common, "getrawtransaction", coinbase)
            if raw.returncode != 0:
                return Outcome(TestResult.FAIL,
                               "getrawtransaction failed, so txindex is not usable",
                               {"stderr": raw.stderr.strip()[:200]})
            rest = _cli(environment, datadir, *common, "getblockcount")
            if rest.returncode != 0:
                return Outcome(TestResult.FAIL, "the node stopped answering RPC")
        finally:
            _stop_node(environment, datadir, process)
    return Outcome(TestResult.PASS,
                   "txindex answered for a confirmed transaction and the node stayed "
                   "responsive with assetindex and rest enabled")


@test("core-unit-test-suite", "core")
def _core_unit_tests(environment: Environment) -> Outcome:
    if environment.source_dir is None:
        return Outcome(TestResult.UNAVAILABLE, "no source checkout was supplied")
    binary = environment.source_dir / "src" / "test" / "test_raven"
    if not binary.exists():
        return Outcome(TestResult.UNAVAILABLE,
                       "the candidate's own test binary was not built, so its "
                       "regression suite could not be observed")
    suites = ("pow_tests", "validation_block_tests", "assets_tests", "checkpoints_tests")
    passed = []
    for suite in suites:
        completed = environment.run([str(binary), f"--run_test={suite}"], timeout=1800)
        if completed.returncode != 0:
            return Outcome(TestResult.FAIL, f"candidate suite {suite} failed",
                           {"suite": suite, "tail": completed.stdout[-400:]})
        passed.append(suite)
    return Outcome(TestResult.PASS, "candidate consensus suites passed",
                   {"suites": passed})


# --------------------------------------------------------------------- harness
def _load_fixtures(environment: Environment) -> Optional[dict]:
    return environment.fixtures


@test("kawpow-header-shape", "harness")
def _header_shape(environment: Environment) -> Outcome:
    fixtures = _load_fixtures(environment)
    if not fixtures:
        return Outcome(TestResult.UNAVAILABLE, "fixtures were not supplied")
    for entry in fixtures["validHeaders"]:
        header = bytes.fromhex(entry["headerHex"])
        if len(header) != KAWPOW_HEADER_SIZE:
            return Outcome(TestResult.FAIL, "a valid fixture is not 120 bytes",
                           {"height": entry["height"], "size": len(header)})
        declared = struct.unpack_from("<I", header, DECLARED_HEIGHT_OFFSET)[0]
        if declared != entry["height"]:
            return Outcome(TestResult.FAIL,
                           "declared height does not sit at byte offset 76",
                           {"height": entry["height"], "declared": declared})
    return Outcome(TestResult.PASS,
                   "every real boundary fixture is a 120 byte header declaring its own "
                   "height at offset 76",
                   {"vectors": len(fixtures["validHeaders"])})


@test("nheight-binding-rejects-forged", "harness")
def _forged_rejected(environment: Environment) -> Outcome:
    fixtures = _load_fixtures(environment)
    if not fixtures:
        return Outcome(TestResult.UNAVAILABLE, "fixtures were not supplied")
    try:
        from electrumx.lib.coins import CoinError, Ravencoin
    except ImportError:
        return Outcome(TestResult.UNAVAILABLE,
                       "the enforcement implementation is not importable here")
    validate = environment.validator or Ravencoin.validate_header
    rejected = []
    for entry in fixtures["invalidHeaders"]:
        header = bytes.fromhex(entry["headerHex"])
        try:
            validate(header, entry["chainHeight"])
        except CoinError:
            rejected.append(entry["name"])
        else:
            return Outcome(TestResult.FAIL,
                           "a forged fixture was accepted by the enforcement rule",
                           {"fixture": entry["name"]})
    return Outcome(TestResult.PASS, "every forged fixture was rejected",
                   {"rejected": rejected})


@test("post-boundary-valid-accepted", "harness")
def _valid_accepted(environment: Environment) -> Outcome:
    fixtures = _load_fixtures(environment)
    if not fixtures:
        return Outcome(TestResult.UNAVAILABLE, "fixtures were not supplied")
    try:
        from electrumx.lib.coins import Ravencoin
        from electrumx.lib.hash import hash_to_hex_str
    except ImportError:
        return Outcome(TestResult.UNAVAILABLE,
                       "the enforcement implementation is not importable here")
    validate = environment.validator or Ravencoin.validate_header
    for entry in fixtures["validHeaders"]:
        header = bytes.fromhex(entry["headerHex"])
        validate(header, entry["height"])
        if hash_to_hex_str(Ravencoin.header_hash(header)) != entry["hash"]:
            return Outcome(TestResult.FAIL,
                           "a valid fixture did not re-hash to its known block hash",
                           {"height": entry["height"]})
    return Outcome(TestResult.PASS,
                   "the honest headers at and after the boundary are accepted and "
                   "re-hash to their known block hashes",
                   {"vectors": len(fixtures["validHeaders"])})


# ------------------------------------------------------- tests needing the chain
@test("incident-checkpoint-hash", "core")
def _checkpoint(environment: Environment) -> Outcome:
    if environment.mainnet_datadir is None or environment.binary("raven-cli") is None:
        return Outcome(TestResult.UNAVAILABLE,
                       "no mainnet chain data was supplied, so the candidate could not "
                       "be asked for the checkpoint hash")
    completed = _cli(environment, environment.mainnet_datadir, "getblockhash",
                     str(CHECKPOINT_HEIGHT))
    if completed.returncode != 0:
        return Outcome(TestResult.UNAVAILABLE, "the node did not answer getblockhash")
    observed = completed.stdout.strip()
    if observed != CHECKPOINT_HASH:
        return Outcome(TestResult.FAIL, "checkpoint hash mismatch",
                       {"height": CHECKPOINT_HEIGHT, "expected": CHECKPOINT_HASH,
                        "observed": observed})
    return Outcome(TestResult.PASS, "canonical hash at the incident checkpoint",
                   {"height": CHECKPOINT_HEIGHT, "hash": observed})


@test("transfer-overflow-deployment", "core")
def _transfer_overflow(environment: Environment) -> Outcome:
    if environment.mainnet_datadir is None or environment.binary("raven-cli") is None:
        return Outcome(TestResult.UNAVAILABLE, "no mainnet chain data was supplied")
    completed = _cli(environment, environment.mainnet_datadir, "getblockchaininfo")
    if completed.returncode != 0:
        return Outcome(TestResult.UNAVAILABLE, "getblockchaininfo did not answer")
    parsed = json.loads(completed.stdout)
    deployments = parsed.get("bip9_softforks", {})
    entry = deployments.get("transfer_overflow")
    if entry is None:
        return Outcome(TestResult.FAIL,
                       "the candidate does not know the transfer_overflow deployment, "
                       "which is how an unpatched generation behaves")
    return Outcome(TestResult.PASS, "transfer_overflow deployment is known",
                   {"status": entry.get("status"), "since": entry.get("since")})


@test("chainstate-rebuild", "core")
def _chainstate_rebuild(environment: Environment) -> Outcome:
    return Outcome(TestResult.UNAVAILABLE,
                   "a chainstate rebuild is a long running job and is only run by the "
                   "dedicated pipeline stage; it is never assumed to have passed")


@test("consensus-divergence-review", "advisory")
def _divergence(environment: Environment) -> Outcome:
    if environment.source_dir is None:
        return Outcome(TestResult.UNAVAILABLE, "no source checkout was supplied")
    baseline = os.environ.get("CERTIFY_BASELINE_COMMIT")
    if not baseline:
        return Outcome(TestResult.UNAVAILABLE, "no baseline commit was configured")
    completed = environment.run(
        ["git", "-C", str(environment.source_dir), "diff", "--stat",
         f"{baseline}..{environment.candidate.commit}", "--", "src/consensus",
         "src/validation.cpp", "src/pow.cpp", "src/primitives", "src/chainparams.cpp"],
        timeout=300)
    if completed.returncode != 0:
        return Outcome(TestResult.UNAVAILABLE, "the diff could not be computed")
    summary = completed.stdout.strip()
    return Outcome(TestResult.PASS if not summary else TestResult.PASS,
                   "consensus-relevant diff summarised for human review",
                   {"baseline": baseline, "diffstat": summary[-1200:] or "no changes"})


# --------------------------------------------------------------------- driver
def certify(candidate: Candidate, profile: dict, environment: Environment) -> dict:
    started = time.time()
    results = {}
    details = {}
    for entry in profile["tests"]:
        test_id = entry["id"]
        function, scope = REGISTRY.get(test_id, (None, None))
        if function is None:
            outcome = Outcome(TestResult.UNAVAILABLE,
                              "the profile requires a test this harness does not "
                              "implement")
            scope = "unknown"
        else:
            try:
                outcome = function(environment)
            except Exception as exc:  # noqa: BLE001
                outcome = Outcome(TestResult.ERROR, f"{type(exc).__name__}: {exc}")
        results[test_id] = outcome.result
        details[test_id] = {"class": entry["class"], "scope": scope,
                            **outcome.to_dict()}

    advisory_ids = {entry["id"] for entry in profile["tests"]
                    if entry["class"] == "advisory"}
    required = tuple(test_id for test_id in required_test_ids(
        profile,
        have_chain_data=environment.mainnet_datadir is not None,
        artifact_pinned=candidate.artifact_sha256 is not None,
    ) if test_id not in advisory_ids)

    state = aggregate_state(results, required_ids=required)
    if candidate.is_known_unsafe_version:
        state = CandidateState.KNOWN_UNSAFE

    report = {
        "schemaVersion": 1,
        "candidate": candidate.to_dict(),
        "profile": profile["profileId"],
        "harnessVersion": HARNESS_VERSION,
        "buildEnvironment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "requiredTests": list(required),
        "results": details,
        "overall": state.value,
        "startedAt": int(started),
        "finishedAt": int(time.time()),
    }
    report["reportDigest"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report


HARNESS_VERSION = "1.0.0"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-file", required=True,
                        help="JSON candidate record from discover_releases.py")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--fixtures", default=None)
    parser.add_argument("--source-dir", default=None)
    parser.add_argument("--bin-dir", default=None)
    parser.add_argument("--mainnet-datadir", default=None)
    parser.add_argument("--artifact", default=None)
    parser.add_argument("--report", required=True)
    arguments = parser.parse_args(argv)

    candidate = Candidate.from_dict(
        json.loads(pathlib.Path(arguments.candidate_file).read_text(encoding="utf-8")))
    profile = load_profile(arguments.profile)
    fixtures = None
    if arguments.fixtures:
        fixtures = json.loads(pathlib.Path(arguments.fixtures).read_text(encoding="utf-8"))

    environment = Environment(
        candidate=candidate,
        source_dir=pathlib.Path(arguments.source_dir) if arguments.source_dir else None,
        bin_dir=pathlib.Path(arguments.bin_dir) if arguments.bin_dir else None,
        mainnet_datadir=(pathlib.Path(arguments.mainnet_datadir)
                         if arguments.mainnet_datadir else None),
        artifact=pathlib.Path(arguments.artifact) if arguments.artifact else None,
        fixtures=fixtures,
    )
    report = certify(candidate, profile, environment)
    pathlib.Path(arguments.report).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{candidate.identity} -> {report['overall']}")
    for test_id, entry in sorted(report["results"].items()):
        print(f"  {entry['result']:<12} {test_id} ({entry['scope']})")
    return 0 if report["overall"] == CandidateState.CERTIFICATION_PASSED.value else 1


if __name__ == "__main__":
    sys.exit(main())
