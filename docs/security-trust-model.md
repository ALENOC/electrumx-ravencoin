Discovery and Backend Trust Model
==================================

This document describes how a Ravencoin Electrum endpoint moves from
"we heard about it" to "an operator decided to trust it", and why no new
signed per-peer capability advertisement protocol was added to get there.

The four states
----------------

The pipeline never collapses these into one another:

1. **DISCOVERED** - an endpoint was named by a seed list, a signed
   directory, or another server's gossip. This says nothing about whether
   it exists, answers, or is honest.
2. **CAPABILITY_SUPPORTED** - the endpoint answered `server.ravencoin_backend`.
   This is a self-report from a third party who can simply lie about it
   (`network_observer/classify.py`, module docstring, rule 1). It decides only
   whether the endpoint is worth checking further.
3. **BACKEND_VERIFIED** - the endpoint's self-reported Core repository and
   commit match a certified-release policy entry (`classify_backend`),
   *and* its chain evidence was actually compared against an independent
   group or a trusted reference and agreed (`compare_chains`,
   `is_corroborated`). Reaching a certified commit alone is not enough:
   `classify_backend` never returns better than `UNVERIFIED` on its own.
4. **TRUSTED_BY_OPERATOR** - an operator's own configuration
   (`BackendIdentity.from_config` in `electrumx/server/ravencoin_backend.py`)
   decided to run against a specific Core build, optionally pinning an
   artifact digest for `BUILD_IDENTITY_VERIFIED`. This is local
   configuration, never something a remote peer can grant itself.

Explicit invariants held throughout:

- Seed-list membership is not trust. `network_observer/config/seeds.json` is a
  discovery hint; every entry is probed and classified like any other
  candidate.
- Answering `server.ravencoin_backend` is not trust. It is a claim that
  still has to survive certified-release lookup and chain comparison.
- Reporting Core >= 4.8.0 is not `BUILD_IDENTITY_VERIFIED`. Identity
  evidence levels (`IdentityEvidence.VERSION_ONLY` / `ATTESTED` /
  `BUILD_VERIFIED`) are set from local deployment configuration, never
  from a value the daemon echoes back at runtime. A remote peer cannot
  elevate its own evidence level.
- A height strictly ahead of the corroborated comparison anchor is never
  itself verification, no matter how internally consistent it looks in
  isolation (`_verified_groups` in `network_observer/classify.py`). The highest
  self-reported height is used only as a starting point for conflict
  detection, never as proof of agreement.

Why no new signed per-peer capability advertisement
-----------------------------------------------------

The question this session set out to answer: does the existing model
(feature probing + signed discovery + backend evidence) already suffice,
or is a new signed-per-peer-advertisement protocol needed?

The decisive evidence is negative, not just architectural preference. An
earlier independent audit (SRV-04/R-01, R-02) found a real bypass: an
attacker could self-report a certified `repository@commit`, an arbitrary
height above a configured `--reference` anchor, and an arbitrary tip, and
be promoted to `SAFE` because `compare_chains` never validated
observations strictly ahead of the anchor. That bypass has since been
fixed (`network_observer/classify.py`, commit `c758aa95`): every observation's
`checkpoint_hash` is now checked against the network's pinned incident
checkpoint regardless of anchor position, and `_verified_groups` never
credits a group whose height is ahead of the corroborated anchor as
agreement.

The fix that closed this gap was **pinning each observation against a
locally-held constant** (`Ravencoin.INCIDENT_CHECKPOINT_HASH`), not a
signature. A per-peer signature would let a peer sign its own self-report
- it does not bind the claim to reality any better than the self-report
already did. The missing primitive was comparison against ground truth,
which the codebase already had a place to add (a per-observation field),
not a new trust root, new protocol version, or new key-management surface.

Given that, and consistent with the existing "no overengineering" instinct
in this codebase (no PKI, no custom certs, no gossip protocol, no peer
attestation framework), a new signed per-peer capability advertisement
protocol was not implemented. It would introduce a second signing
identity per operator, a second verification path a legacy client has no
way to know about, and would still not solve the actual problem, which is
verifying claims against reality, not authenticating who made the claim.

What is signed today, and what it is not
------------------------------------------

`network_observer/directory.py` signs a *snapshot of this operator's own
classification*, not a per-peer credential. Ed25519, canonical
`sort_keys`/compact-separator serialization, a pinned external key,
`schemaVersion` fixed at 1, monotonic `directoryVersion` for rollback
protection, duplicate-entry rejection, required-field validation on each
entry, and expiry are all enforced in `verify_directory`. Unknown fields
on an entry are tolerated, not rejected: this is deliberate forward
compatibility so an older wallet does not fail closed the moment a newer
publisher adds a field it does not understand (see `test_j_unknown_...`
in `tests/test_trust_model_scenarios.py`), while every field this code
does understand is still fully validated.

The code is explicit that this is not itself trust:
`network_observer/directory.py`'s module docstring calls it "a discovery hint and
nothing more", and `candidates_from_directory` "deliberately returns
candidates, not approved servers". A wallet or operator still has to run
its own checks; a signed directory only prevents the snapshot from being
tampered with or replayed in transit.

The monitor / server boundary
-------------------------------

`electrumx/server/peers.py` (the server's own Electrum-protocol peer
gossip) and `network_observer/` (the discovery, classification, and signed
directory pipeline) are intentionally two separate systems with no
imports between them. `on_peers_subscribe` returns endpoints the server
has gossiped with in the last 24 hours, with no trust weighting, which is
correct for what it is: Electrum peer gossip is discovery-only by
protocol, and the client on the other end is expected to apply its own
judgment, exactly as `network_observer/` does for wallets that choose to consult
its signed directory instead. Wiring monitor's classification into the
server's own gossip responses would turn a discovery mechanism into an
enforcement mechanism the Electrum protocol was never designed to carry,
which is the kind of new peer-attestation framework this session's
constraints explicitly ruled out absent a concrete demonstrated threat.
No such threat was found; the boundary is documented here instead of
closed.

Peer selection preference
----------------------------

`network_observer/config/seeds.json` lists `electrumx.raventag.com` (the reference
deployment of this codebase) first, then the Cipig operator group, then
`rvn4lyfe.com` and other upstream defaults. This is a preference hint for
where to try first, never a trust decision: every entry, first or last, is
probed and classified identically. List order must never bypass the
classification/trust policy applied to every candidate alike.
