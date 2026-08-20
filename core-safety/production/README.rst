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

Policy v1 and v2 are retained as immutable historical evidence. The currently
published ``safe-core-policy.json`` is still signed policy v2 and therefore is
not the final RavenProject-only production policy. The reviewed
``safe-core-policy-v3.unsigned.json`` candidate revokes the historical
``2miners/Ravencoin`` identity and certifies the exact official
``RavenProject/Ravencoin`` v4.8.0 commit used by ``compose.yaml``; it must remain
unsigned/non-current until the protected Core-policy signing procedure signs
that exact body and the resulting document is reviewed and promoted.

The dedicated ``security/ravenproject-only-core-source`` branch is the signing
execution boundary. A normal developer or automation commit on other branches
cannot turn the unsigned candidate into production policy; the migration
workflow must pass its deterministic pre-sign check and the protected
``core-safety-signing`` environment before any signed v3 artifact is published.

The single-file installer pins the Core-policy public key independently from
the ElectrumX release/update key. It rejects a release unless the bundled
policy verifies under that pinned key and the manifest's exact Core repository,
commit, tag, version, policy version and certification report digest all match
one ``KNOWN_SAFE`` policy entry. Until signed policy v3 is promoted, a real
production release for the RavenProject Core pin is therefore expected to fail
closed. Local end-to-end validation uses explicitly non-production ephemeral
keys and cannot promote or replace either production trust root.
