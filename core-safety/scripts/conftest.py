# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""PR3 fixture completion for security-script tests.

This file changes test inputs only. Assertions in ``test_installer.py`` remain
unchanged: its synthetic v2 release bundles are completed with the runtime
ChainStrap files that became mandatory in PR3, allowing the pre-existing tests
to reach the same security gates they were written to exercise.
"""

from __future__ import annotations

from pathlib import Path

import pytest


PR3_RUNTIME_BUNDLE_FILES = {
    "contrib/bootstrap/chainstrap_bootstrap.py": b"# synthetic transport fixture\n",
    "contrib/bootstrap/chainstrap_runtime.py": b"# synthetic runtime resolver fixture\n",
    "docker/bootstrap/Dockerfile": b"FROM scratch\n",
}


@pytest.fixture(autouse=True)
def complete_pr3_installer_bundle_inputs(request, monkeypatch):
    module = request.module
    module_path = Path(str(getattr(module, "__file__", "")))
    if module_path.name != "test_installer.py" or not hasattr(module, "bundle_files"):
        return

    original = module.bundle_files

    def complete_bundle_files(*args, **kwargs):
        files = original(*args, **kwargs)
        for path, data in PR3_RUNTIME_BUNDLE_FILES.items():
            if path in files:
                raise AssertionError(f"PR3 fixture unexpectedly already defines {path}")
            files[path] = data
        return files

    monkeypatch.setattr(module, "bundle_files", complete_bundle_files)
