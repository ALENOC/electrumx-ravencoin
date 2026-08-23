# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression coverage for mixed-content ChainStrap archives.

Current upstream RVN snapshots may carry derived datadir material (for example
``assets/*.ldb`` or ``blocks/index/*.ldb``) next to raw block files.  The
bootstrap trust boundary remains raw ``blocks/blk*.dat`` only: safe foreign
material is ignored and never extracted no matter where it sits in the archive,
while unsafe paths and unsafe entry types still fail closed.
"""
from __future__ import annotations

import stat
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "contrib" / "bootstrap"
sys.path.insert(0, str(BOOTSTRAP))

import chainstrap_runtime as runtime  # noqa: E402


def _zip(path: Path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)


def test_current_upstream_shape_ignores_assets_and_extracts_only_raw_blocks(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("assets/000111.ldb", b"derived-index-1"),
        ("assets/000112.ldb", b"derived-index-2"),
        ("assets/000157.log", b"derived-log"),
        ("assets/CURRENT", b"MANIFEST-000001\n"),
        ("assets/LOCK", b""),
        ("blocks/blk00000.dat", b"zero"),
        ("blocks/blk00001.dat", b"one"),
    ])

    vetted = runtime.preflight_archive(archive, already_claimed=set())
    assert [info.filename for info in vetted] == [
        "blocks/blk00000.dat",
        "blocks/blk00001.dat",
    ]

    datadir = tmp_path / "data"
    extracted = runtime.extract_preflighted_archive(
        archive, datadir, vetted, existing_uncompressed=0)
    assert [path.name for path in extracted] == ["blk00000.dat", "blk00001.dat"]
    assert sorted(path.name for path in (datadir / "blocks").iterdir()) == [
        "blk00000.dat",
        "blk00001.dat",
    ]
    assert not (datadir / "assets").exists()


def test_archive_with_only_foreign_members_is_refused(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("assets/000111.ldb", b"derived"),
        ("assets/CURRENT", b"MANIFEST-000001\n"),
    ])
    with pytest.raises(runtime.RuntimeBootstrapError, match="no allowlisted raw block"):
        runtime.preflight_archive(archive, already_claimed=set())


def test_foreign_traversal_is_refused_before_ignore(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("../assets/000111.ldb", b"x"),
        ("blocks/blk00000.dat", b"good"),
    ])
    with pytest.raises(runtime.RuntimeBootstrapError, match="unsafe.*path"):
        runtime.preflight_archive(archive, already_claimed=set())


def test_foreign_symlink_is_refused_before_ignore(tmp_path):
    archive = tmp_path / "part.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        link = zipfile.ZipInfo("assets/CURRENT")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "../../wallet.dat")
        zf.writestr("blocks/blk00000.dat", b"good")
    with pytest.raises(runtime.RuntimeBootstrapError, match="unsafe.*type"):
        runtime.preflight_archive(archive, already_claimed=set())


# --- 1.13.8 regression: safe derived members under blocks/ ------------------
#
# A real fresh install of 1.13.7 aborted on part 14/17 of the current upstream
# snapshot because it contained ``blocks/index/004089.ldb``.  Location alone
# must not be fatal: classification is structural.

SAFE_FOREIGN_NAMES = [
    "assets/foo.ldb",
    "assets/CURRENT",
    "assets/LOCK",
    "chainstate/foo",
    "blocks/index/004089.ldb",
    "blocks/index/CURRENT",
    "blocks/index/MANIFEST-000001",
    "blocks/rev00000.dat",
    "indexes/txindex/000123.ldb",
    "peers.dat",
]


@pytest.mark.parametrize("foreign", SAFE_FOREIGN_NAMES)
def test_safe_foreign_member_is_ignored_not_extracted(tmp_path, foreign):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        (foreign, b"derived"),
        ("blocks/blk00000.dat", b"good"),
    ])

    vetted = runtime.preflight_archive(archive, already_claimed=set())
    assert [info.filename for info in vetted] == ["blocks/blk00000.dat"]

    datadir = tmp_path / "data"
    runtime.extract_preflighted_archive(
        archive, datadir, vetted, existing_uncompressed=0)
    written = sorted(
        str(path.relative_to(datadir))
        for path in datadir.rglob("*")
        if path.is_file()
    )
    assert written == ["blocks/blk00000.dat"]


def test_blocks_index_member_reproduces_the_reported_failure(tmp_path):
    """Exact real-world reproduction from ChainStrap part 14/17."""
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("blocks/blk04088.dat", b"a"),
        ("blocks/index/004089.ldb", b"leveldb-derived"),
        ("blocks/index/CURRENT", b"MANIFEST-000001\n"),
        ("blocks/index/LOCK", b""),
        ("blocks/blk04089.dat", b"b"),
    ])

    vetted = runtime.preflight_archive(archive, already_claimed=set())
    assert [info.filename for info in vetted] == [
        "blocks/blk04088.dat",
        "blocks/blk04089.dat",
    ]

    datadir = tmp_path / "data"
    extracted = runtime.extract_preflighted_archive(
        archive, datadir, vetted, existing_uncompressed=0)
    assert [path.name for path in extracted] == ["blk04088.dat", "blk04089.dat"]
    assert not (datadir / "blocks" / "index").exists()
    assert sorted(path.name for path in (datadir / "blocks").iterdir()) == [
        "blk04088.dat",
        "blk04089.dat",
    ]


def test_all_safe_foreign_shapes_together_still_extract_only_raw_blocks(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [(name, b"derived") for name in SAFE_FOREIGN_NAMES] + [
        ("blocks/blk00000.dat", b"zero"),
        ("blocks/blk00001.dat", b"one"),
        ("blocks/blk00002.dat", b"two"),
    ])

    vetted = runtime.preflight_archive(archive, already_claimed=set())
    assert [info.filename for info in vetted] == [
        "blocks/blk00000.dat",
        "blocks/blk00001.dat",
        "blocks/blk00002.dat",
    ]

    datadir = tmp_path / "data"
    runtime.extract_preflighted_archive(
        archive, datadir, vetted, existing_uncompressed=0)
    assert sorted(
        str(path.relative_to(datadir))
        for path in datadir.rglob("*") if path.is_file()
    ) == ["blocks/blk00000.dat", "blocks/blk00001.dat", "blocks/blk00002.dat"]


def test_archive_with_only_blocks_index_members_is_refused(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("blocks/index/004089.ldb", b"derived"),
        ("blocks/index/CURRENT", b"MANIFEST-000001\n"),
    ])
    with pytest.raises(runtime.RuntimeBootstrapError, match="no allowlisted raw block"):
        runtime.preflight_archive(archive, already_claimed=set())


@pytest.mark.parametrize("unsafe", [
    "../blocks/blk00000.dat",
    "/blocks/blk00000.dat",
    "blocks/../../blk00000.dat",
    "blocks/index/../../../004089.ldb",
])
def test_unsafe_paths_fail_closed(tmp_path, unsafe):
    archive = tmp_path / "part.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo(unsafe)
        zf.writestr(info, b"x")
        zf.writestr("blocks/blk00000.dat", b"good")
    with pytest.raises(runtime.RuntimeBootstrapError, match="unsafe.*path"):
        runtime.preflight_archive(archive, already_claimed=set())


def test_backslash_member_path_fails_closed(tmp_path):
    archive = tmp_path / "part.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(zipfile.ZipInfo("blocks\\index\\004089.ldb"), b"x")
        zf.writestr("blocks/blk00000.dat", b"good")
    with pytest.raises(runtime.RuntimeBootstrapError, match="unsafe.*path"):
        runtime.preflight_archive(archive, already_claimed=set())


def test_symlink_inside_blocks_index_fails_closed(tmp_path):
    archive = tmp_path / "part.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        link = zipfile.ZipInfo("blocks/index/CURRENT")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "../../../wallet.dat")
        zf.writestr("blocks/blk00000.dat", b"good")
    with pytest.raises(runtime.RuntimeBootstrapError, match="unsafe.*type"):
        runtime.preflight_archive(archive, already_claimed=set())


@pytest.mark.parametrize("mode", [stat.S_IFBLK, stat.S_IFCHR, stat.S_IFIFO, stat.S_IFSOCK])
def test_special_foreign_entries_fail_closed(tmp_path, mode):
    archive = tmp_path / "part.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        special = zipfile.ZipInfo("blocks/index/special")
        special.create_system = 3
        special.external_attr = (mode | 0o666) << 16
        zf.writestr(special, b"")
        zf.writestr("blocks/blk00000.dat", b"good")
    with pytest.raises(runtime.RuntimeBootstrapError, match="unsafe.*type"):
        runtime.preflight_archive(archive, already_claimed=set())


def test_encrypted_foreign_member_is_not_classified_as_safe():
    """An encrypted foreign member is never inert: it fails the safe predicate.

    ``ZipFile.writestr`` rewrites the general purpose flags, so the encryption
    bit is asserted directly on the classification primitive.
    """
    info = zipfile.ZipInfo("blocks/index/004089.ldb")
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    assert runtime._foreign_member_is_safe_to_ignore(info)
    info.flag_bits |= 0x1
    assert not runtime._foreign_member_is_safe_to_ignore(info)


@pytest.mark.parametrize("bad", [
    "blocks/blk0000.dat",
    "blocks/blk000000000.dat",
    "blocks/blk0000a.dat",
    "blocks/blk00000.dat.tmp",
    "blocks/sub/blk00000.dat",
    "BLOCKS/blk00000.dat",
    "./blocks/blk00000.dat",
])
def test_block_lookalike_names_are_not_extracted(tmp_path, bad):
    """Names that miss the allowlist are ignored, never extracted."""
    archive = tmp_path / "part.zip"
    _zip(archive, [
        (bad, b"lookalike"),
        ("blocks/blk00000.dat", b"good"),
    ])
    vetted = runtime.preflight_archive(archive, already_claimed=set())
    assert [info.filename for info in vetted] == ["blocks/blk00000.dat"]

    datadir = tmp_path / "data"
    runtime.extract_preflighted_archive(
        archive, datadir, vetted, existing_uncompressed=0)
    assert sorted(
        str(path.relative_to(datadir))
        for path in datadir.rglob("*") if path.is_file()
    ) == ["blocks/blk00000.dat"]


def test_duplicate_eligible_block_path_fails_closed(tmp_path):
    archive = tmp_path / "part.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("blocks/blk00000.dat", b"one")
        zf.writestr("blocks/blk00000.dat", b"two")
    with pytest.raises(runtime.RuntimeBootstrapError, match="duplicate"):
        runtime.preflight_archive(archive, already_claimed=set())


def test_duplicate_block_number_fails_closed(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("blocks/blk00000.dat", b"one"),
        ("blocks/blk000000.dat", b"two"),
    ])
    with pytest.raises(runtime.RuntimeBootstrapError, match="duplicate block number"):
        runtime.preflight_archive(archive, already_claimed=set())


def test_block_already_claimed_by_another_part_fails_closed(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("blocks/index/004089.ldb", b"derived"),
        ("blocks/blk00000.dat", b"good"),
    ])
    with pytest.raises(runtime.RuntimeBootstrapError, match="duplicate block file"):
        runtime.preflight_archive(archive, already_claimed={"blk00000.dat"})


def test_ignored_members_do_not_count_toward_extraction_totals(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("blocks/index/004089.ldb", b"x" * 4096),
        ("blocks/blk00000.dat", b"good"),
    ])
    vetted = runtime.preflight_archive(archive, already_claimed=set())
    assert sum(info.file_size for info in vetted) == len(b"good")


def test_runtime_policy_is_installed_on_the_base_module(tmp_path):
    """Base entrypoint must resolve the shim policy, not the legacy selector."""
    import chainstrap_runtime_base as base

    # Other test modules may load a second copy of the shim under its own
    # spec, so re-install this copy's policy before checking the binding.
    runtime._install_policy_override()
    assert base.preflight_archive is runtime.preflight_archive

    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("blocks/index/004089.ldb", b"derived"),
        ("blocks/blk00000.dat", b"good"),
    ])
    vetted = base.preflight_archive(archive, already_claimed=set())
    assert [info.filename for info in vetted] == ["blocks/blk00000.dat"]


def test_legacy_transport_extractor_also_ignores_blocks_index(tmp_path):
    """Shared semantics: the legacy gateway extractor must not diverge."""
    import chainstrap_bootstrap as legacy

    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("blocks/index/004089.ldb", b"derived"),
        ("assets/CURRENT", b"MANIFEST-000001\n"),
        ("blocks/blk00000.dat", b"good"),
    ])
    datadir = tmp_path / "legacy-data"
    extracted = legacy.extract_block_files(archive, datadir)
    assert [path.name for path in extracted] == ["blk00000.dat"]
    assert sorted(
        str(path.relative_to(datadir))
        for path in datadir.rglob("*") if path.is_file()
    ) == ["blocks/blk00000.dat"]
