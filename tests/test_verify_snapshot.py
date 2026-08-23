import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
BOOTSTRAP = ROOT / "contrib" / "bootstrap"
CHAINSTRAP = ROOT / "contrib" / "chainstrap"
sys.path.insert(0, str(BOOTSTRAP))

import chainstrap_entrypoint
import chainstrap_runtime

VERIFY_PATH = CHAINSTRAP / "verify-snapshot.py"
SPEC = importlib.util.spec_from_file_location("verify_snapshot", VERIFY_PATH)
verify_snapshot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify_snapshot
SPEC.loader.exec_module(verify_snapshot)


def make_zip(path: Path, members: dict[str, bytes]) -> tuple[int, str]:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    raw = path.read_bytes()
    return len(raw), hashlib.sha256(raw).hexdigest()


def write_upstream(path: Path, parts: list[dict], *, height=123, blockhash=None):
    if blockhash is None:
        blockhash = "1" * 64
    manifest = {
        "chain": "RVN",
        "mode": "mainnet",
        "blocks": height,
        "blockhash": blockhash,
        "updated": "2026-08-22T00:00:00Z",
        "bytes": sum(part["bytes"] for part in parts),
        "parts": parts,
        "ipfs_hashes": [part["cid"] for part in parts],
        "baseurl": "https://attacker.example/ipfs/",
    }
    path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")


def test_verify_snapshot_emits_reviewed_floor_and_evidence(tmp_path):
    archives = tmp_path / "archives"
    archives.mkdir()
    cid0 = "Qm" + "A" * 44
    cid1 = "Qm" + "B" * 44
    size0, sha0 = make_zip(archives / f"{cid0}.zip", {"blocks/blk00000.dat": b"zero"})
    size1, sha1 = make_zip(archives / f"{cid1}.zip", {"blocks/blk00001.dat": b"one"})
    parts = [
        {"cid": cid0, "bytes": size0, "sha256": sha0},
        {"cid": cid1, "bytes": size1, "sha256": sha1},
    ]
    upstream = tmp_path / "upstream.json"
    floor = tmp_path / "floor.json"
    evidence = tmp_path / "evidence.json"
    write_upstream(upstream, parts)

    result = verify_snapshot.verify_snapshot(
        upstream, "a" * 40, archives, floor, evidence)

    floor_doc = json.loads(floor.read_text(encoding="utf-8"))
    evidence_doc = json.loads(evidence.read_text(encoding="utf-8"))
    assert set(floor_doc) == {
        "chain", "mode", "blocks", "blockhash", "updated", "bytes", "source", "parts"}
    assert floor_doc["source"] == {
        "repository": "chainstrap/chainstrap.github.io",
        "commit": "a" * 40,
        "path": "RVN/RVN-mainnet.json",
    }
    assert result["blockFileCount"] == 2
    assert evidence_doc["verification"] == "independent-local-payload"
    assert evidence_doc["firstBlock"] == 0
    assert evidence_doc["lastBlock"] == 1
    assert evidence_doc["reviewedManifestSha256"] == hashlib.sha256(floor.read_bytes()).hexdigest()


def test_verify_snapshot_rejects_foreign_member(tmp_path):
    archives = tmp_path / "archives"
    archives.mkdir()
    cid = "Qm" + "C" * 44
    size, digest = make_zip(
        archives / f"{cid}.zip",
        {"blocks/blk00000.dat": b"ok", "wallet.dat": b"forbidden"},
    )
    upstream = tmp_path / "upstream.json"
    write_upstream(upstream, [{"cid": cid, "bytes": size, "sha256": digest}])
    with pytest.raises(verify_snapshot.VerificationError, match="non-allowlisted ZIP member"):
        verify_snapshot.verify_snapshot(
            upstream, "b" * 40, archives, tmp_path / "floor.json", tmp_path / "evidence.json")


def test_verify_snapshot_rejects_non_contiguous_global_blocks(tmp_path):
    archives = tmp_path / "archives"
    archives.mkdir()
    cid = "Qm" + "D" * 44
    size, digest = make_zip(
        archives / f"{cid}.zip",
        {"blocks/blk00000.dat": b"zero", "blocks/blk00002.dat": b"two"},
    )
    upstream = tmp_path / "upstream.json"
    write_upstream(upstream, [{"cid": cid, "bytes": size, "sha256": digest}])
    with pytest.raises(verify_snapshot.VerificationError, match="not contiguous"):
        verify_snapshot.verify_snapshot(
            upstream, "c" * 40, archives, tmp_path / "floor.json", tmp_path / "evidence.json")


def test_release_floor_file_is_load_bearing_for_runtime(tmp_path):
    reviewed = ROOT / "contrib/bootstrap/manifests/rvn-mainnet-2026-08-19.json"
    floor, digest = chainstrap_entrypoint.verify_floor_binding(reviewed)
    assert floor["blocks"] == chainstrap_runtime.RELEASE_FLOOR_HEIGHT
    assert floor["blockhash"] == chainstrap_runtime.RELEASE_FLOOR_BLOCKHASH
    assert digest == hashlib.sha256(reviewed.read_bytes()).hexdigest()

    modified = json.loads(reviewed.read_text(encoding="utf-8"))
    modified["blocks"] += 1
    bad = tmp_path / "floor.json"
    bad.write_text(json.dumps(modified) + "\n", encoding="utf-8")
    with pytest.raises(chainstrap_entrypoint.FloorBindingError, match="reviewed floor height"):
        chainstrap_entrypoint.verify_floor_binding(bad)
