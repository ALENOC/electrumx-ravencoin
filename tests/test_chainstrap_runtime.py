import datetime
import hashlib
import importlib.util
import json
import pathlib
import sys
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP_DIR = ROOT / "contrib" / "bootstrap"
sys.path.insert(0, str(BOOTSTRAP_DIR))

SPEC = importlib.util.spec_from_file_location(
    "chainstrap_runtime", BOOTSTRAP_DIR / "chainstrap_runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)


def upstream_payload(*, height=4_504_348,
                     blockhash="00" * 32,
                     updated="2026-08-22T00:48:15Z"):
    parts = [{
        "cid": "Qm" + "A" * 44,
        "bytes": 10,
        "sha256": "a" * 64,
    }]
    return {
        "chain": "RVN",
        "mode": "mainnet",
        "blocks": height,
        "blockhash": blockhash,
        "updated": updated,
        "bytes": 10,
        "parts": parts,
        "ipfs_hashes": [parts[0]["cid"]],
        "baseurl": "https://attacker.example/ipfs/",
    }


def test_runtime_sanitizer_ignores_upstream_gateway_fields():
    payload = upstream_payload()
    manifest = runtime.sanitize_upstream_manifest(
        payload,
        commit="1" * 40,
        require_fresh=True,
        now=datetime.datetime(2026, 8, 22, 1, 0, tzinfo=datetime.timezone.utc),
    )
    assert "baseurl" not in manifest
    assert "ipfs_hashes" not in manifest
    assert manifest["source"] == {
        "repository": "chainstrap/chainstrap.github.io",
        "commit": "1" * 40,
        "path": "RVN/RVN-mainnet.json",
    }
    assert runtime.transport.DEFAULT_GATEWAYS == (
        "https://ipfs.io/ipfs/",
        "https://dweb.link/ipfs/",
        "https://w3s.link/ipfs/",
        "https://gateway.pinata.cloud/ipfs/",
    )


def test_runtime_master_staleness_fails_closed_but_exact_pin_can_be_reviewed():
    payload = upstream_payload(updated="2026-08-01T00:00:00Z")
    now = datetime.datetime(2026, 8, 22, tzinfo=datetime.timezone.utc)
    with pytest.raises(runtime.RuntimeBootstrapError, match="stale"):
        runtime.sanitize_upstream_manifest(
            payload, commit="1" * 40, require_fresh=True, now=now)
    manifest = runtime.sanitize_upstream_manifest(
        payload, commit="1" * 40, require_fresh=False, now=now)
    assert manifest["blocks"] == payload["blocks"]


def test_release_floor_is_load_bearing_before_download():
    below = upstream_payload(height=runtime.RELEASE_FLOOR_HEIGHT - 1)
    with pytest.raises(runtime.RuntimeBootstrapError, match="below release floor"):
        runtime.sanitize_upstream_manifest(
            below, commit="1" * 40, require_fresh=False)

    wrong_floor = upstream_payload(
        height=runtime.RELEASE_FLOOR_HEIGHT, blockhash="f" * 64)
    with pytest.raises(runtime.RuntimeBootstrapError, match="release-floor"):
        runtime.sanitize_upstream_manifest(
            wrong_floor, commit="1" * 40, require_fresh=False)


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)


def test_foreign_member_rejects_entire_archive_before_extraction(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("blocks/blk00000.dat", b"good"),
        ("blocks/rev00000.dat", b"forbidden"),
    ])
    with pytest.raises(runtime.RuntimeBootstrapError, match="non-allowlisted"):
        runtime.preflight_archive(archive, already_claimed=set())
    assert not (tmp_path / "data" / "blocks" / "blk00000.dat").exists()


def test_traversal_directory_and_symlink_members_are_rejected(tmp_path):
    traversal = tmp_path / "traversal.zip"
    _zip(traversal, [("../blocks/blk00000.dat", b"x")])
    with pytest.raises(runtime.RuntimeBootstrapError, match="unsafe.*path"):
        runtime.preflight_archive(traversal, already_claimed=set())

    directory = tmp_path / "directory.zip"
    with zipfile.ZipFile(directory, "w") as archive:
        archive.writestr("blocks/", b"")
        archive.writestr("blocks/blk00000.dat", b"x")
    with pytest.raises(runtime.RuntimeBootstrapError, match="non-allowlisted"):
        runtime.preflight_archive(directory, already_claimed=set())

    symlink = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink, "w") as archive:
        info = zipfile.ZipInfo("blocks/blk00000.dat")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, "../../wallet.dat")
    with pytest.raises(runtime.RuntimeBootstrapError, match="unsafe.*type"):
        runtime.preflight_archive(symlink, already_claimed=set())


def test_duplicate_block_number_across_parts_is_rejected(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [("blocks/blk00001.dat", b"x")])
    with pytest.raises(runtime.RuntimeBootstrapError, match="across ChainStrap parts"):
        runtime.preflight_archive(archive, already_claimed={"blk00001.dat"})


def test_preflight_then_extract_keeps_only_raw_blocks(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("blocks/blk00000.dat", b"zero"),
        ("blocks/blk00001.dat", b"one"),
    ])
    vetted = runtime.preflight_archive(archive, already_claimed=set())
    extracted = runtime.extract_preflighted_archive(
        archive, tmp_path / "data", vetted, existing_uncompressed=0)
    assert [path.name for path in extracted] == ["blk00000.dat", "blk00001.dat"]
    assert (tmp_path / "data" / "blocks" / "blk00000.dat").read_bytes() == b"zero"


def _marker():
    manifest = {
        "chain": "RVN",
        "mode": "mainnet",
        "blocks": runtime.RELEASE_FLOOR_HEIGHT,
        "blockhash": runtime.RELEASE_FLOOR_BLOCKHASH,
        "updated": "2026-08-19T22:16:38Z",
        "bytes": 10,
        "source": {
            "repository": runtime.UPSTREAM_REPOSITORY,
            "commit": "1" * 40,
            "path": runtime.UPSTREAM_PATH,
        },
        "parts": [{
            "cid": "Qm" + "A" * 44,
            "bytes": 10,
            "sha256": "a" * 64,
        }],
    }
    return runtime.marker_payload(
        manifest, "b" * 64, resolution_mode="exact-commit")


def test_completed_node_never_resolves_upstream_again(tmp_path):
    datadir = tmp_path / "rvn"
    blocks = datadir / "blocks"
    blocks.mkdir(parents=True)
    (blocks / "blk00000.dat").write_bytes(b"block")
    marker_path = datadir / runtime.BLOCKS_MARKER
    runtime.transport.write_json_atomic(marker_path, _marker())
    (datadir / runtime.REINDEX_MARKER).write_text(
        runtime.transport.sha256_file(marker_path) + "\n", encoding="ascii")

    def forbidden_opener(*_args, **_kwargs):
        raise AssertionError("completed bootstrap must not perform upstream I/O")

    assert runtime.main(["--datadir", str(datadir)], opener=forbidden_opener) == 0


def test_resolution_lock_binds_exact_commit_and_metadata_digest(tmp_path):
    manifest = {
        "chain": "RVN",
        "mode": "mainnet",
        "blocks": runtime.RELEASE_FLOOR_HEIGHT,
        "blockhash": runtime.RELEASE_FLOOR_BLOCKHASH,
        "updated": "2026-08-19T22:16:38Z",
        "bytes": 10,
        "source": {
            "repository": runtime.UPSTREAM_REPOSITORY,
            "commit": "2" * 40,
            "path": runtime.UPSTREAM_PATH,
        },
        "parts": [{
            "cid": "Qm" + "A" * 44,
            "bytes": 10,
            "sha256": "a" * 64,
        }],
    }
    marker = runtime.marker_payload(
        manifest, hashlib.sha256(b"metadata").hexdigest(), resolution_mode="exact-commit")
    lock = tmp_path / runtime.RESOLUTION_LOCK
    runtime.transport.write_json_atomic(
        lock, runtime._resolution_lock_payload(manifest, marker))
    loaded_manifest, loaded_marker = runtime._load_resolution_lock(lock)
    assert loaded_manifest["source"]["commit"] == "2" * 40
    assert loaded_marker["metadata_sha256"] == hashlib.sha256(b"metadata").hexdigest()


def test_core_reindex_script_requires_release_floor_ancestry():
    script = (ROOT / "docker" / "core" / "bootstrap-reindex.sh").read_text(encoding="utf-8")
    assert "release_floor_height=4501329" in script
    assert runtime.RELEASE_FLOOR_BLOCKHASH in script
    assert 'observed_floor_hash=$(rpc getblockhash "$release_floor_height"' in script
    assert "Release-floor ancestry verified" in script
    assert "-reindex=1" in script
    assert "-assumevalid=0" in script
    assert script.count("-connect=0") >= 2


def test_bootstrap_image_uses_runtime_resolver_with_bound_release_floor():
    dockerfile = (ROOT / "docker" / "bootstrap" / "Dockerfile").read_text(encoding="utf-8")
    assert "chainstrap_runtime.py" in dockerfile
    assert "chainstrap_entrypoint.py" in dockerfile
    assert "manifests/rvn-mainnet-2026-08-19.json" in dockerfile
    assert "/opt/electrumx-ravencoin/bootstrap/release-floor.json" in dockerfile
    assert "CHAINSTRAP_RELEASE_FLOOR=/opt/electrumx-ravencoin/bootstrap/release-floor.json" in dockerfile
    assert 'ENTRYPOINT ["chainstrap-bootstrap"]' in dockerfile
    assert 'CMD ["--datadir", "/var/lib/ravencoin"]' in dockerfile
    assert "--manifest" not in dockerfile
