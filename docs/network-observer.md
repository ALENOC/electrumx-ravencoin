# The Ravencoin Electrum Network Observer (Phase 1)

The Network Observer is the evolution of the existing monitor into a
consensus-aware observation layer. It is NOT a Ravencoin consensus
authority and never becomes one: the Ravencoin blockchain remains the
consensus authority, every snapshot says so on its face, and clients
must independently validate endpoints. The architecture audit this
evolution started from is `docs/network-observer-audit.md`.

## What exists now, module by module

* `monitor/quorum.py`: Chain Quorum 2.0. Shared-height anchor selection,
  deterministic plus nonce-derived challenge heights, structured
  verdicts (VALID, TEMPORARY_LAG, INSUFFICIENT_CORROBORATION,
  CHALLENGE_INCOMPLETE, CONFLICT_SUSPECTED, CHAIN_CONFLICT).
* `monitor/observer.py`: signed observation bundles (Ed25519, domain
  separated), anti-replay (per-observer sequence high-water, expiry,
  clock-skew tolerance), local-only observer key generation.
* `monitor/operators.py`: cryptographic operator identity. Signed
  operator declarations with sequence anti-rollback and the trust
  states UNKNOWN / SELF_SIGNED / REGISTRY_ATTESTED / INVALID / EXPIRED.
* `monitor/vantage.py`: multi-vantage aggregation and selective-serving
  categories (MULTI_VANTAGE_CONSISTENT, DNS_VARIANCE, TLS_VARIANCE,
  BACKEND_IDENTITY_VARIANCE, CHAIN_SELECTIVE_SERVING_SUSPECTED,
  DATA_SELECTIVE_SERVING_SUSPECTED).
* `monitor/assets.py`: active asset capability probes and Asset Data
  Quorum v1 (canonical digests, height-bound comparison, verdicts
  AGREE / MISMATCH_SUSPECTED / CONFLICT / INSUFFICIENT_QUORUM /
  NOT_COMPARABLE / UNSUPPORTED).
* `monitor/snapshot.py`: the signed network observation snapshot
  (separate schema from the signed directory, which is unchanged).
* `monitor/store.py`: schema 5, forward-safe migration, bounded
  retention that never prunes a security high-water mark.
* Index lag (backend Core height minus ElectrumX tip) is classified in
  `monitor/classify.classify_index_lag` as INDEX_SYNCED / LAGGING /
  STALE / UNKNOWN, an operational axis strictly separate from Security.

## The security model, restated

* Availability is not security. Reachability and safety remain
  categorical and separate; no numeric score ever affects a security
  verdict.
* Discovery does not create trust. Seeds, registries and gossip produce
  candidates that are validated independently.
* Server self-reports are claims. `server.ravencoin_backend` decides
  whether an endpoint is worth checking, never that it is safe.
* Endpoint majority is never consensus. Quorum is counted over
  independent ATTESTED operator identities. One operator with twenty
  hostnames is one operator; twenty self-signed keys are zero trusted
  operators; unknown endpoints never inflate diversity.
* Absence of conflict is not agreement. SAFE needs positive
  corroboration; missing evidence yields CHALLENGE_INCOMPLETE or
  INSUFFICIENT_CORROBORATION, never silence read as consent.
* Chain evidence outranks self-reported identity.
* Fail closed everywhere: malformed, untrusted, expired, replayed or
  rolled-back inputs are refused outright.

## Chain Quorum 2.0 algorithm

Servers sit at different heights, so tips are not comparable. The
anchor is the k-th highest height reported by distinct attested
operator groups (k = `minimum_attested_groups_for_anchor`, default 2),
minus `stable_height_margin` (default 6 blocks). One absurd future
height therefore cannot self-anchor consensus, and a lagging server
drags the anchor down only when it is itself in the top k. The
challenge set is the anchor, the anchor minus 6/60/720 blocks (minutes,
hour, half-day at one block per minute), the pinned incident checkpoint
when the anchor is at or above it, and `random_challenges` (default 2)
heights derived from SHA256 over a per-crawl CSPRNG nonce. The nonce is
persisted with the round, so selection is auditable; a server cannot
precompute the quiz. Real Ravencoin header hashes (KAWPOW semantics)
are computed locally from returned headers; malformed headers are no
evidence. Two attested groups returning different valid hashes at the
same height are mutual conflict evidence; confirmation across
independent crawls (threshold 2, as before) escalates
CONFLICT_SUSPECTED to CHAIN_CONFLICT. A group strictly above the
corroborated evidence remains unverified for the uncorroborated
portion: the anchor never validates itself.

## Multi-vantage observers

Each observer runs this monitor on an unrelated network and publishes a
signed bundle (`monitor observe` flow, `verify-observation`,
`aggregate-observations`). The aggregator trusts only observer keys it
configured itself; an observation signing its own key proves nothing.
Replay protection: sequence must exceed the persisted per-key
high-water mark, timestamps must sit inside a 300-second tolerated
skew, bundles expire, and nothing older than 24 hours is current.
Observer diversity and operator diversity are two axes and are never
summed: three observers over one CIPIG endpoint are reported as exactly
that. Selective serving is surfaced, not adjudicated: DNS legitimately
varies (CDNs, failover), so DNS_VARIANCE is an observation;
CHAIN_SELECTIVE_SERVING_SUSPECTED needs different observers to receive
different valid block hashes for the same height in one window.

## Operator identity and registry migration

The configured operatorGroup registry keeps working unchanged.
Cryptographic declarations are preferred when available. Precedence:
REGISTRY_ATTESTED declaration > configured group > UNKNOWN-* placeholder.
SELF_SIGNED declarations verify but never count toward independent
quorum: key generation is free, trust is not. Declarations carry
sequence numbers with rollback refusal and validity windows. A future
optional DNS TXT hint may strengthen ownership evidence; it would be a
hint, never trust, and requires nothing (no DNSSEC, no public DNS) for
Phase 1.

## Index lag

`indexLag = backend.blocks - electrumx height`, both from one probe
round. Classified SYNCED (<=0 extra blocks), LAGGING (<=6), STALE
(>60), UNKNOWN (missing either side). A stale index makes an endpoint a
poor wallet server, never an unsafe Core; a certified Core is never
UNSAFE for lagging above it. Thresholds are configurable in
`Thresholds`.

## Asset capability and Asset Data Quorum v1

Capability is actively probed, not trusted from `"assets": true`:
`monitor/config/asset-sentinels.json` (shipped EMPTY by default;
operators opt in with cheap, public, permanent queries; never create
assets, never spend RVN). Results form a per-method matrix and the
classes ASSET_CAPABLE / PARTIAL / UNSUPPORTED / UNKNOWN / LEGACY (a
flag without working methods).

Asset data quorum detects an ElectrumX server serving asset data
inconsistent with independent servers on the same chain. Comparison is
height-bound: the `*_history` RPCs return entries with confirmed
heights deterministically ordered by (height, tx_hash), so requesting
history with `include_mempool=false` and folding entries with
`height <= H` reconstructs confirmed state at H exactly (proven from
`electrumx/server/db.py`; see the audit). Samples are canonicalized
with per-type domain separation (mappings key-sorted, history order
preserved) and digested with SHA-256. Comparison requires comparable
chain context (a non-conflicting Chain Quorum round at that height),
identical (type, sentinel, height) across samples, and at least two
attested operators. Different heights are NOT_COMPARABLE, never a
mismatch. One differing crawl is MISMATCH_SUSPECTED; only repeated
comparable observations escalate to ASSET_DATA_CONFLICT. Asset
conflicts are their own failure domain: they make an endpoint unsuitable
for asset wallets but never reuse Security.CONFLICT, which belongs to
chain consensus.

## What asset quorum can and cannot prove

It can prove that independent attested operators served the same
canonical confirmed state for the sampled sentinels at one shared
height. It cannot prove anything about sentinels not sampled, about
mempool state (excluded on purpose: not comparable across servers),
about operators that did not answer, or about heights above the anchor.

## Operational deployment, keys, recovery

`observer-keygen` writes a local Ed25519 keypair (private key 0600,
never printed, committed or published). Rotation: generate a new key,
add its public key to aggregators' trusted-observer files, retire the
old line; sequences are per key id, so a new key starts at 1 by design.
Lost observer key: generate a new one and have aggregators attest it;
a lost observer key cannot be recovered and must be retired, because
sequence anti-replay is bound to the key id. Snapshot signing uses a
separate local key and its own version high-water mark.

## Rate-limit implications

One crawl round = one probe connection plus one challenge connection
per reachable endpoint (a handful of header fetches) plus one asset
connection when sentinels are configured, all inside the existing
per-host hourly budget and bounded response limits. No expensive
methods (`list_addresses_by_asset`) are ever probed.

## The `server.ravencoin_backend` compatibility contract

`server.ravencoin_backend` is a production public API consumed by
deployed RavenTag Android clients and is NOT touched by this work: it
still describes local ElectrumX to Core evidence, with its existing
fields, types and semantics. Network Observer functionality lives in
the monitor package, is never imported by the server, and the RPC has
no dependency on observers, quorum, registries or anything beyond its
existing local checks. `tests/test_ravencoin_backend_contract.py`
locks this contract in and fails the release if it regresses.

## Threat model delta

New surfaces and their answers: Sybil operator keys (SELF_SIGNED never
quorum), future-height self-anchoring (k-th highest anchor), challenge
precomputation (CSPRAG nonce, auditable), equivocation/selective
serving (multi-vantage cross-comparison, conservative categories),
bundle replay/rollback (sequence high-water, expiry, skew bounds),
declaration rollback (per-key sequence marks that survive pruning),
snapshot rollback (version high-water), asset-data gaslighting
(height-bound canonical digests over attested operators with
cross-crawl confirmation), database poisoning (schema version refusal,
transactional migrations, bounded retention). Known limitation, stated
plainly: with exactly two attested operators, a hash or asset-data
disagreement identifies a conflict between them but cannot say which
one lies; the monitor reports exactly that and never guesses.
