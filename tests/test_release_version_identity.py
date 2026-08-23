"""Guard the 1.13.8 release identity.

1.13.2 and 1.13.3 were built, failed real hardware qualification and were
withdrawn. Historical qualification/signing material may name those candidates;
release identity surfaces for the replacement candidate must pin 1.13.8.
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "1.13.8"
WITHDRAWN_VERSION = "1.13.2"
PREVIOUS_WITHDRAWN_VERSION = "1.13.3"

# Historical-by-design: these record withdrawn candidates on purpose.
HISTORICAL_FILES = {
    "docs/release-artifact-revisions.md",
    "docs/HARDWARE_QUALIFICATION_1.13.3.md",
    "docs/OFFLINE_RELEASE_SIGNING_1.13.3.md",
    "tests/test_update_staging_compose_resolution.py",
}

# Version-ordering fixtures use neighbouring version strings as data, not as
# release identity.
ORDERING_FIXTURE_FILES = {
    "tests/test_artifact_revision.py",
}


def tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
    return [name for name in out.stdout.decode().split("\0") if name]


def executable_surface():
    """Tracked files that ship or run, excluding docs and tests."""
    for name in tracked_files():
        if name in HISTORICAL_FILES or name in ORDERING_FIXTURE_FILES:
            continue
        if name.startswith("docs/") or name.startswith("tests/"):
            continue
        if Path(name).name.startswith("test_"):
            continue
        yield name


def test_no_release_executable_reference_to_1_13_2_candidate():
    offenders = []
    for name in executable_surface():
        path = ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if WITHDRAWN_VERSION in line:
                offenders.append(f"{name}:{number}: {line.strip()}")
    assert not offenders, (
        "release-executable references to the withdrawn 1.13.2 candidate:\n"
        + "\n".join(offenders))


@pytest.mark.parametrize("relative,pattern", [
    ("electrumx/__init__.py", r"^version = 'ElectrumX-RVN 1\.13\.8'$"),
    ("compose.yaml", r"image: alenoc/electrumx-ravencoin:1\.13\.8$"),
    ("compose.existing-core.yaml", r"image: alenoc/electrumx-ravencoin:1\.13\.8$"),
    ("core-safety/scripts/legacy_1_13_1_apply.py",
     r'^TARGET_ELECTRUMX_VERSION = "1\.13\.8"$'),
    ("core-safety/scripts/render_installer_v2.py",
     r"'\"alenoc/electrumx-ravencoin:1\.13\.8\", \"-ec\",'"),
    (".github/workflows/release.yml", r"default: v1\.13\.8$"),
])
def test_release_identity_is_pinned_to_current_version(relative, pattern):
    text = (ROOT / relative).read_text(encoding="utf-8")
    assert re.search(pattern, text, re.MULTILINE), \
        f"{relative} does not pin {RELEASE_VERSION} ({pattern})"


def test_v1_installer_template_needle_is_not_bumped():
    """The render source needle must keep matching the reviewed 1.13.1 template."""
    template = (ROOT / "electrumx-ravencoin-install.py").read_text(encoding="utf-8")
    renderer = (ROOT / "core-safety/scripts/render_installer_v2.py").read_text(
        encoding="utf-8")
    assert '"alenoc/electrumx-ravencoin:1.13.1", "-ec",' in template
    assert "'\"alenoc/electrumx-ravencoin:1.13.1\", \"-ec\",'" in renderer


def test_qualification_and_signing_docs_track_the_current_release():
    assert (ROOT / f"docs/HARDWARE_QUALIFICATION_{RELEASE_VERSION}.md").is_file()
    assert (ROOT / f"docs/OFFLINE_RELEASE_SIGNING_{RELEASE_VERSION}.md").is_file()
    # 1.13.3 remains as historical evidence of the failed qualification.
    assert (ROOT / f"docs/HARDWARE_QUALIFICATION_{PREVIOUS_WITHDRAWN_VERSION}.md").is_file()
    assert (ROOT / f"docs/OFFLINE_RELEASE_SIGNING_{PREVIOUS_WITHDRAWN_VERSION}.md").is_file()
    assert not (ROOT / f"docs/HARDWARE_QUALIFICATION_{WITHDRAWN_VERSION}.md").exists()
    assert not (ROOT / f"docs/OFFLINE_RELEASE_SIGNING_{WITHDRAWN_VERSION}.md").exists()


def test_current_qualification_records_chainstrap_mixed_content_contract():
    text = (ROOT / f"docs/HARDWARE_QUALIFICATION_{RELEASE_VERSION}.md").read_text(
        encoding="utf-8")
    assert "## RESULT: PENDING" in text
    assert "source version for the ordinary updater path: `1.13.7`" in text
    assert "candidate version: `1.13.8`" in text
    assert "`assets/LOCK`" in text
    assert "`blocks/index/004089.ldb`" in text
    assert "`blocks/blk*.dat`" in text
    assert "only allowlisted `blocks/blk*.dat` members may be extracted" in text
    assert "Ravencoin Core still performs a local full reindex/revalidation" in text


def test_superseded_1_13_7_release_docs_are_retained():
    """1.13.7 material stays as history: the tag/release is never rewritten."""
    assert (ROOT / "docs/HARDWARE_QUALIFICATION_1.13.7.md").is_file()
    assert (ROOT / "docs/OFFLINE_RELEASE_SIGNING_1.13.7.md").is_file()
