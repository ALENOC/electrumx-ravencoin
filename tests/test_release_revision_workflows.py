from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github/workflows/release.yml"
SIGNING = ROOT / ".github/workflows/signing.yml"
DOC = ROOT / "docs/release-artifact-revisions.md"


def test_release_workflow_cannot_sign_or_publish():
    text = RELEASE.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in text
    assert "secrets.ELECTRUMX_UPDATE_SIGNING_KEY" not in text
    assert 'test -z "${ELECTRUMX_UPDATE_SIGNING_KEY:-}"' in text
    assert "contents: write" not in text
    assert "gh release create" not in text
    assert "gh release edit" not in text
    assert "build_production_release.py" in text
    assert "--artifact-revision" in text
    assert "--update-public-key-hex" in text
    assert "test ! -e release-manifest.json" in text


def test_signing_workflow_is_handoff_only():
    text = SIGNING.read_text(encoding="utf-8")
    assert "actions: read" in text
    assert "contents: read" in text
    assert "secrets.ELECTRUMX_UPDATE_SIGNING_KEY" not in text
    assert "contents: write" not in text
    assert "gh release create" not in text
    assert "pkeyutl -sign" not in text
    assert "OFFLINE_SIGNING_REVIEW.json" in text
    assert "release-manifest.canonical" in text


def test_docs_state_manual_1_13_1_to_manifest_v2_transition():
    text = DOC.read_text(encoding="utf-8")
    assert "1.13.1 node **cannot and must not directly" in text
    assert "auto-update to a manifest-v2 release, including 1.13.11**" in text
    assert "out-of-band" in text
    assert "retired key does not sign, certify, endorse, or attest its own replacement" in text


def test_docs_record_withdrawn_1_13_2_candidate():
    text = DOC.read_text(encoding="utf-8")
    assert "## Withdrawn 1.13.2 candidate" in text
    assert "failed real hardware qualification" in text
    assert "was never published as an installable release" in text
    assert "`v1.13.2` is retained only as a historical trace" in text


def test_docs_bind_one_root_owned_high_water_locator():
    text = DOC.read_text(encoding="utf-8")
    assert "/var/lib/electrumx-ravencoin/security-state.locator" in text
    assert "/var/lib/electrumx-ravencoin/security-state.json" in text
    assert "${XDG_STATE_HOME:-$HOME/.local/state}/electrumx-ravencoin/security-state.json" in text
    assert "mode `0644`" in text
    assert "mode `0600`" in text
    assert "highestAcceptedVersion" in text
