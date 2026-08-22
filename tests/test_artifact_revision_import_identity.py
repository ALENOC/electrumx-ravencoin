import pathlib
import sys

ROOT = pathlib.Path(__file__).parents[1]
SCRIPTS = ROOT / "core-safety/scripts"
sys.path.insert(0, str(SCRIPTS))

from electrumx_core_safety import artifact_revision as canonical_ar  # noqa: E402
import artifact_revision as legacy_ar  # noqa: E402
import electrumx_update_cli as cli  # noqa: E402
import update_decision as ud  # noqa: E402


def test_artifact_revision_module_has_one_canonical_identity():
    assert legacy_ar is canonical_ar
    assert ud.artifact_revision is canonical_ar
    assert cli.artifact_revision is canonical_ar

    canonical_path = pathlib.Path(canonical_ar.__file__).resolve()
    loaded_objects = {
        id(module): module
        for module in sys.modules.values()
        if module is not None and getattr(module, "__file__", None) and
        pathlib.Path(module.__file__).resolve() == canonical_path
    }
    assert list(loaded_objects.values()) == [canonical_ar]
    assert canonical_ar.EligibilityVerdict.__module__ == \
        "electrumx_core_safety.artifact_revision"
