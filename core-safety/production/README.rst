Public safe-Core policy distribution
====================================

The maintained static policy URL is::

  https://raw.githubusercontent.com/ALENOC/electrumx-ravencoin/master/core-safety/production/safe-core-policy.json

This is a static HTTPS distribution path, not a GitHub API request. The wallet
must verify the Ed25519 signature and policy version before accepting it. A
transport outage is non-fatal: the last verified cache and built-in baseline
remain in force, and an unknown Core release is never accepted because the URL
is unavailable.

The detached signature and pinned public-key metadata are published beside the
policy for auditability. The inline signature in the JSON is authoritative for
client verification.

Policy v1 is retained as historical evidence. The current policy is v2 because
the immutable profile revision and digest were added to the signed certification
metadata.
