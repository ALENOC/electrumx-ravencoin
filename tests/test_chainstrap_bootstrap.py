import importlib.util
import json
import stat
import tempfile
import zipfile
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "contrib"
    / "bootstrap"
    / "chainstrap_bootstrap.py"
)
spec = importlib.util.spec_from_file_location("chainstrap_bootstrap", MODULE_PATH)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def manifest():
    return {
        "chain": "RVN",
        "mode": "mainnet",
        "blocks": 123,
        "blockhash": "00" * 32,
        "bytes": 10,
        "source": {
            "repository": "chainstrap/chainstrap.github.io",
            "commit": "1" * 40,
            "path": "RVN/RVN-mainnet.json",
        },
        "parts": [
            {
                "cid": "Qm" + "A" * 44,
                "bytes": 10,
                "sha256": "a" * 64,
            }
        ],
    }


def test_manifest_is_rvn_mainnet_only():
    data = manifest()
    bootstrap.validate_manifest(data)
    data["mode"] = "testnet"
    try:
        bootstrap.validate_manifest(data)
    except ValueError as exc:
        assert "RVN mainnet" in str(exc)
    else:
        raise AssertionError("testnet manifest was accepted")


def test_extracts_only_raw_block_files():
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        archive = root / "part.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("blocks/blk00000.dat", b"block-zero")
            handle.writestr("blocks/blk00001.dat", b"block-one")
            handle.writestr("blocks/rev00000.dat", b"undo")
            handle.writestr("blocks/index/000001.ldb", b"index")
            handle.writestr("chainstate/000001.ldb", b"state")
            handle.writestr("assets/000001.ldb", b"asset")
            handle.writestr("../../wallet.dat", b"never")

        extracted = bootstrap.extract_block_files(archive, root / "data")
        assert [path.name for path in extracted] == ["blk00000.dat", "blk00001.dat"]
        assert (root / "data/blocks/blk00000.dat").read_bytes() == b"block-zero"
        assert not (root / "data/chainstate").exists()
        assert not (root / "wallet.dat").exists()


def test_rejects_symlink_disguised_as_block_file():
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        archive = root / "part.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            link = zipfile.ZipInfo("blocks/blk99999.dat")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            handle.writestr(link, "../../wallet.dat")
        try:
            bootstrap.extract_block_files(archive, root / "data")
        except RuntimeError as exc:
            assert "unsafe block archive entry" in str(exc)
        else:
            raise AssertionError("symlink block entry was accepted")


def test_contiguous_block_files_are_required():
    with tempfile.TemporaryDirectory() as tempdir:
        blocks = Path(tempdir) / "blocks"
        blocks.mkdir()
        (blocks / "blk00000.dat").write_bytes(b"0")
        (blocks / "blk00002.dat").write_bytes(b"2")
        try:
            bootstrap.validate_contiguous_blocks(Path(tempdir))
        except RuntimeError as exc:
            assert "gap" in str(exc)
        else:
            raise AssertionError("block-file gap was accepted")


def test_existing_unmarked_blocks_are_rejected():
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        blocks = root / "blocks"
        blocks.mkdir()
        (blocks / "blk00000.dat").write_bytes(b"existing")
        marker = bootstrap.marker_payload(manifest(), "f" * 64)
        try:
            bootstrap.check_existing_state(root, marker, {manifest()["parts"][0]["cid"]})
        except RuntimeError as exc:
            assert "fresh data volume" in str(exc)
        else:
            raise AssertionError("unmarked existing blocks were accepted")


def test_matching_marker_makes_bootstrap_idempotent():
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        blocks = root / "blocks"
        blocks.mkdir()
        (blocks / "blk00000.dat").write_bytes(b"existing")
        marker = bootstrap.marker_payload(manifest(), "f" * 64)
        (root / bootstrap.BLOCKS_MARKER).write_text(json.dumps(marker), encoding="utf-8")
        assert bootstrap.check_existing_state(
            root, marker, {manifest()["parts"][0]["cid"]}
        )[0] is True


def test_completed_prior_snapshot_does_not_block_future_manifest_upgrade():
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        old_data = manifest()
        old_marker = bootstrap.marker_payload(old_data, "e" * 64)
        marker_path = root / bootstrap.BLOCKS_MARKER
        bootstrap.write_json_atomic(marker_path, old_marker)
        (root / bootstrap.REINDEX_MARKER).write_text(
            bootstrap.sha256_file(marker_path) + "\n", encoding="utf-8"
        )

        new_data = manifest()
        new_data["blocks"] += 100
        new_marker = bootstrap.marker_payload(new_data, "f" * 64)
        ready, completed, resuming = bootstrap.check_existing_state(
            root, new_marker, {new_data["parts"][0]["cid"]}
        )
        assert ready is True
        assert completed == set()
        assert resuming is False


def test_tampered_completed_reindex_marker_is_rejected():
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        data = manifest()
        marker = bootstrap.marker_payload(data, "f" * 64)
        bootstrap.write_json_atomic(root / bootstrap.BLOCKS_MARKER, marker)
        (root / bootstrap.REINDEX_MARKER).write_text("0" * 64 + "\n", encoding="utf-8")
        try:
            bootstrap.check_existing_state(
                root, marker, {data["parts"][0]["cid"]}
            )
        except RuntimeError as exc:
            assert "does not match" in str(exc)
        else:
            raise AssertionError("tampered completed-reindex marker was accepted")


def test_existing_non_block_core_state_is_rejected():
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        chainstate = root / "chainstate"
        chainstate.mkdir()
        (chainstate / "000001.ldb").write_bytes(b"existing")
        data = manifest()
        marker = bootstrap.marker_payload(data, "f" * 64)
        try:
            bootstrap.check_existing_state(
                root, marker, {data["parts"][0]["cid"]}
            )
        except RuntimeError as exc:
            assert "fresh data volume" in str(exc)
        else:
            raise AssertionError("existing Core state was accepted")


def test_matching_progress_marker_allows_safe_resume():
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        blocks = root / "blocks"
        blocks.mkdir()
        (blocks / "blk00000.dat").write_bytes(b"partial-bootstrap")
        data = manifest()
        marker = bootstrap.marker_payload(data, "f" * 64)
        cid = data["parts"][0]["cid"]
        bootstrap.write_progress(
            root / bootstrap.PROGRESS_MARKER, marker, {cid}, data["parts"]
        )
        ready, completed, resuming = bootstrap.check_existing_state(
            root, marker, {cid}
        )
        assert ready is False
        assert completed == {cid}
        assert resuming is True


def test_progress_from_different_snapshot_is_rejected():
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        data = manifest()
        marker = bootstrap.marker_payload(data, "f" * 64)
        other_marker = dict(marker)
        other_marker["height"] += 1
        bootstrap.write_progress(
            root / bootstrap.PROGRESS_MARKER, other_marker, set(), data["parts"]
        )
        try:
            bootstrap.check_existing_state(
                root, marker, {data["parts"][0]["cid"]}
            )
        except RuntimeError as exc:
            assert "different ChainStrap snapshot" in str(exc)
        else:
            raise AssertionError("progress from another snapshot was accepted")


def test_reindex_stage_keeps_full_validation_flags():
    root = Path(__file__).resolve().parents[1]
    script = (root / "docker/core/bootstrap-reindex.sh").read_text(encoding="utf-8")
    for required in (
        "-reindex=1",
        "-assumevalid=0",
        "-txindex=1",
        "-assetindex=1",
        "-stopafterblockimport=1",
    ):
        assert required in script

    compose = (root / "compose.chainstrap.yaml").read_text(encoding="utf-8")
    assert "network_mode: none" in compose
    assert "service_completed_successfully" in compose


def test_plain_http_gateway_is_rejected():
    try:
        bootstrap.gateway_urls("Qm" + "A" * 44, "http://example.invalid/ipfs/")
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("plain HTTP gateway was accepted")


def test_fast_bootstrap_wrapper_enables_compose_override(tmp_path):
    import os
    import subprocess

    root = Path(__file__).resolve().parents[1]
    wrapper = root / "fast-bootstrap.sh"
    work = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    work.mkdir()
    bin_dir.mkdir()
    (work / "fast-bootstrap.sh").write_bytes(wrapper.read_bytes())
    (work / "fast-bootstrap.sh").chmod(0o755)
    (work / "setup.sh").write_text(
        "#!/bin/sh\nset -eu\nprintf 'ELECTRUMX_CACHE_MB=1200\\n' > .env\n",
        encoding="utf-8",
    )
    (work / "setup.sh").chmod(0o755)
    (bin_dir / "docker").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "docker").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    subprocess.run(["./fast-bootstrap.sh"], cwd=work, env=env, check=True)
    assert "COMPOSE_FILE=compose.yaml:compose.chainstrap.yaml" in (work / ".env").read_text()


def test_unlisted_https_gateway_is_rejected():
    try:
        bootstrap.gateway_urls("Qm" + "A" * 44, "https://127.0.0.1/ipfs/")
    except ValueError as exc:
        assert "allowlist" in str(exc)
    else:
        raise AssertionError("unlisted HTTPS gateway was accepted")


def test_progress_helpers_are_human_readable():
    assert bootstrap.format_bytes(1024 * 1024) == "1.0 MiB"
    assert bootstrap.format_rate(12 * 1024 * 1024) == "12.0 MiB/s"
    assert bootstrap.format_duration(65) == "1m05s"
    assert bootstrap.format_duration(3661) == "1h01m01s"


def test_status_helper_exposes_bootstrap_phases():
    root = Path(__file__).resolve().parents[1]
    helper = (root / "fast-bootstrap-status.sh").read_text(encoding="utf-8")
    for required in (
        "CHAINSTRAP DOWNLOAD / EXTRACTION",
        "CORE 4.8.0 OFFLINE FULL REINDEX",
        "ELECTRUMX INDEXING / ONLINE",
        "docker stats --no-stream",
        "docker compose logs -f",
    ):
        assert required in helper


class _FakeHTTPResponse:
    def __init__(self, url, status, headers, reads):
        self._url = url
        self.status = status
        self.headers = headers
        self._reads = iter(reads)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def geturl(self):
        return self._url

    def getcode(self):
        return self.status

    def read(self, _size):
        item = next(self._reads, b"")
        if isinstance(item, BaseException):
            raise item
        return item


def test_transport_drop_preserves_partial_and_next_request_resumes(monkeypatch, tmp_path):
    partial = tmp_path / "part.zip.part"
    partial.write_bytes(b"abcd")
    url = "https://gateway.pinata.cloud/ipfs/Qm" + "A" * 44
    requests = []
    responses = iter(
        [
            _FakeHTTPResponse(
                url,
                206,
                {"Content-Range": "bytes 4-9/10"},
                [b"ef", bootstrap.URLError("connection dropped")],
            ),
            _FakeHTTPResponse(
                url,
                206,
                {"Content-Range": "bytes 6-9/10"},
                [b"ghij", b""],
            ),
        ]
    )

    def fake_urlopen(request, timeout):
        assert timeout == 120
        requests.append(request)
        return next(responses)

    monkeypatch.setattr(bootstrap, "urlopen", fake_urlopen)
    try:
        bootstrap._download_once(url, partial, 10, 0, 10)
    except bootstrap.URLError:
        pass
    else:
        raise AssertionError("simulated transport drop did not propagate")

    assert partial.read_bytes() == b"abcdef"
    assert requests[0].get_header("Range") == "bytes=4-"

    bootstrap._download_once(url, partial, 10, 0, 10)
    assert requests[1].get_header("Range") == "bytes=6-"
    assert partial.read_bytes() == b"abcdefghij"


def test_bad_content_range_is_rejected_without_touching_partial(monkeypatch, tmp_path):
    partial = tmp_path / "part.zip.part"
    partial.write_bytes(b"abcd")
    url = "https://gateway.pinata.cloud/ipfs/Qm" + "A" * 44
    response = _FakeHTTPResponse(
        url,
        206,
        {"Content-Range": "bytes 0-9/10"},
        [b"should-not-be-written"],
    )
    monkeypatch.setattr(bootstrap, "urlopen", lambda request, timeout: response)

    try:
        bootstrap._download_once(url, partial, 10, 0, 10)
    except bootstrap.ResumeProtocolError as exc:
        assert "resume offset" in str(exc)
    else:
        raise AssertionError("mismatched Content-Range was accepted")

    assert partial.read_bytes() == b"abcd"


def test_gateway_transport_failure_keeps_safe_partial(monkeypatch, tmp_path):
    partial = tmp_path / (("Qm" + "A" * 44) + ".zip.part")
    partial.write_bytes(b"abcd")
    data = b"abcdefghij"
    part = {
        "cid": "Qm" + "A" * 44,
        "bytes": len(data),
        "sha256": __import__("hashlib").sha256(data).hexdigest(),
    }
    url = "https://gateway.pinata.cloud/ipfs/" + part["cid"]
    response = _FakeHTTPResponse(
        url,
        206,
        {"Content-Range": "bytes 4-9/10"},
        [b"ef", bootstrap.URLError("connection dropped")],
    )

    monkeypatch.setattr(bootstrap, "GATEWAY_RETRIES", 1)
    monkeypatch.setattr(bootstrap, "gateway_urls", lambda cid, gateway: [url])
    monkeypatch.setattr(bootstrap, "urlopen", lambda request, timeout: response)

    try:
        bootstrap.download_verified(part, tmp_path, None, 0, len(data))
    except RuntimeError as exc:
        assert "partial was preserved" in str(exc)
    else:
        raise AssertionError("all-gateway failure unexpectedly succeeded")

    assert partial.read_bytes() == b"abcdef"


def test_dweb_and_w3s_use_dns_safe_cidv1_subdomain_urls():
    cid = "QmY5vJPeihekwRdkz7M9ebHR38upLjQBYfHzSv7PpR8EG8"
    urls = bootstrap.gateway_urls(cid, None)
    label = "bafybeieqz3etwfy2hev2iwfmf7c5gn3cs4ma5akiu7yzohiiuatxpa7cxe"
    assert f"https://{label}.ipfs.dweb.link/" in urls
    assert f"https://{label}.ipfs.w3s.link/" in urls
    assert f"https://dweb.link/ipfs/{cid}" not in urls
    assert f"https://w3s.link/ipfs/{cid}" not in urls


def test_redirect_validation_is_scoped_to_original_gateway_family():
    bootstrap._validate_final_gateway_url(
        "https://bafyexample.ipfs.w3s.link/", "w3s.link"
    )
    try:
        bootstrap._validate_final_gateway_url(
            "https://bafyexample.ipfs.dweb.link/", "w3s.link"
        )
    except RuntimeError as exc:
        assert "gateway" in str(exc)
    else:
        raise AssertionError("cross-family redirect was accepted")


def test_gateway_pool_promotes_success_and_defers_zero_progress_failure(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: now[0])
    pool = bootstrap.GatewayPool()
    first = pool.endpoints("QmY5vJPeihekwRdkz7M9ebHR38upLjQBYfHzSv7PpR8EG8")
    assert first[0].display_host == "ipfs.io"

    pool.record_zero_progress_failure(first[0])
    pinata = next(item for item in first if item.display_host == "gateway.pinata.cloud")
    pool.record_success(pinata)

    reordered = pool.endpoints("QmY5vJPeihekwRdkz7M9ebHR38upLjQBYfHzSv7PpR8EG8")
    assert reordered[0].display_host == "gateway.pinata.cloud"
    assert reordered[-1].display_host == "ipfs.io"


def test_zero_progress_failure_skips_redundant_retries(monkeypatch, tmp_path):
    data = b"abcdefghij"
    part = {
        "cid": "Qm" + "A" * 44,
        "bytes": len(data),
        "sha256": __import__("hashlib").sha256(data).hexdigest(),
    }
    url = "https://gateway.pinata.cloud/ipfs/" + part["cid"]
    calls = []

    monkeypatch.setattr(bootstrap, "GATEWAY_RETRIES", 3)
    monkeypatch.setattr(bootstrap, "gateway_urls", lambda cid, gateway: [url])

    def fail_immediately(request, timeout):
        calls.append(request)
        raise bootstrap.URLError("immediate TLS EOF")

    monkeypatch.setattr(bootstrap, "urlopen", fail_immediately)
    try:
        bootstrap.download_verified(part, tmp_path, None, 0, len(data))
    except RuntimeError:
        pass
    else:
        raise AssertionError("failed gateway unexpectedly succeeded")

    assert len(calls) == 1


def test_progress_failure_retries_same_gateway_and_finishes(monkeypatch, tmp_path):
    data = b"abcdefghij"
    part = {
        "cid": "Qm" + "A" * 44,
        "bytes": len(data),
        "sha256": __import__("hashlib").sha256(data).hexdigest(),
    }
    url = "https://gateway.pinata.cloud/ipfs/" + part["cid"]
    responses = iter(
        [
            _FakeHTTPResponse(
                url,
                200,
                {},
                [b"abcd", bootstrap.URLError("connection dropped")],
            ),
            _FakeHTTPResponse(
                url,
                206,
                {"Content-Range": "bytes 4-9/10"},
                [b"efghij", b""],
            ),
        ]
    )
    calls = []

    monkeypatch.setattr(bootstrap, "GATEWAY_RETRIES", 3)
    monkeypatch.setattr(bootstrap, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(bootstrap, "gateway_urls", lambda cid, gateway: [url])

    def fake_urlopen(request, timeout):
        calls.append(request)
        return next(responses)

    monkeypatch.setattr(bootstrap, "urlopen", fake_urlopen)
    archive = bootstrap.download_verified(part, tmp_path, None, 0, len(data))
    assert archive.read_bytes() == data
    assert len(calls) == 2
    assert calls[1].get_header("Range") == "bytes=4-"


def test_progress_eta_waits_for_meaningful_sample():
    warmup = bootstrap.format_progress_rate_eta(
        transferred=1 * 1024 * 1024,
        elapsed=5.0,
        remaining=2 * 1024 * 1024 * 1024,
    )
    assert warmup == "measuring speed..."

    ready_by_bytes = bootstrap.format_progress_rate_eta(
        transferred=64 * 1024 * 1024,
        elapsed=5.0,
        remaining=1024 * 1024 * 1024,
    )
    assert "MiB/s" in ready_by_bytes
    assert "ETA" in ready_by_bytes

    ready_by_time = bootstrap.format_progress_rate_eta(
        transferred=2 * 1024 * 1024,
        elapsed=20.0,
        remaining=1024 * 1024,
    )
    assert "KiB/s" in ready_by_time
    assert "ETA" in ready_by_time


def test_completed_progress_always_reports_rate_and_zero_eta():
    result = bootstrap.format_progress_rate_eta(
        transferred=1024 * 1024,
        elapsed=1.0,
        remaining=0,
        complete=True,
    )
    assert "1.0 MiB/s" in result
    assert "ETA 0s" in result
