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

Policy v1 and v2 are retained as immutable historical evidence. The current
``safe-core-policy.json`` is signed policy v3: it revokes the historical
``2miners/Ravencoin`` identity and certifies the exact official
``RavenProject/Ravencoin`` v4.8.0 commit used by ``compose.yaml``. The detached
``safe-core-policy-v3.sig`` and inline signature both verify under the pinned
Core-policy public key.

The dedicated ``security/ravenproject-only-core-source`` branch was the signing
execution boundary. The migration passed its deterministic pre-sign check and
the protected ``core-safety-signing`` environment generated, verified and
published policy v3 before removing the one-shot migration workflow.

The single-file installer pins the Core-policy public key independently from
the ElectrumX release/update key. It rejects a release unless the bundled
policy verifies under that pinned key and the manifest's exact Core repository,
commit, tag, version, policy version and certification report digest all match
one ``KNOWN_SAFE`` policy entry. Local end-to-end validation uses explicitly
non-production ephemeral keys and cannot promote or replace either production
trust root.
