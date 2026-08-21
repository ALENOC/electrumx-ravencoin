# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Append-only local audit log for electrumx-update actions.

Every apply, whether it succeeds, is refused, or ends in rollback, appends
one JSON line. Nothing here is remote or signed; it exists so an operator can
answer "who/what triggered this and what happened" after the fact.
"""

from __future__ import annotations

import datetime
import json
import os


def record(path: str, *, initiator: str, action: str, old_version: str,
          new_version: str, manifest_digest: str, result: str,
          detail: str = "") -> None:
    entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0).isoformat(),
        "initiator": initiator,
        "action": action,
        "oldVersion": old_version,
        "newVersion": new_version,
        "manifestDigest": manifest_digest,
        "result": result,
        "detail": detail,
    }
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
