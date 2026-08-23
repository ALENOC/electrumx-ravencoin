# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression coverage for mixed-content ChainStrap archives.

Current upstream RVN snapshots may carry derived datadir material (for example
``assets/*.ldb``) next to raw block files.  The bootstrap trust boundary remains
raw ``blocks/blk*.dat`` only: safe foreign material is ignored, never extracted,
while unsafe paths/types and anything unexpected inside the blocks namespace
still fail closed.
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


def test_non_allowlisted_member_inside_blocks_namespace_remains_fail_closed(tmp_path):
    archive = tmp_path / "part.zip"
    _zip(archive, [
        ("blocks/blk00000.dat", b"good"),
        ("blocks/rev00000.dat", b"derived-undo"),
    ])
    with pytest.raises(runtime.RuntimeBootstrapError, match="non-allowlisted"):
        runtime.preflight_archive(archive, already_claimed=set())
