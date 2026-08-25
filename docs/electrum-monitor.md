# Ravencoin Network Observer (was: Electrum monitor)

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Security model](security-model.md)

The optional monitor discovers public Ravencoin Electrum endpoints and records
health and safety evidence. It is not required by the default Compose stack.

## Discovery sources

- bootstrap seeds;
- `server.peers.subscribe` gossip;
- the voluntary operator registry.

Discovery is not trust. Every endpoint is independently probed for health,
TLS, backend capability, policy identity and chain consistency.

## Safety boundaries

SSRF protection rejects loopback, private, link-local, unique-local, reserved,
multicast, documentation and cloud-metadata addresses. Probes are bounded by
depth, candidate count, concurrency, response size and timeouts. A directory is
a signed discovery hint; the client revalidates every endpoint.

## Operators and vantage points

`operatorGroup` counts organizations, not endpoints. Several CIPIG or ALENOC
endpoints are one independent group. Unknown endpoints are not artificially
merged, and an endpoint with no known operator identity never counts toward
independent-operator diversity on its own, no matter how many such hostnames
appear: `status` reports them separately instead. Observations retain a
vantage-point identifier so unreachable-from-one-probe is not overstated as
globally offline.

A backend is promoted from certified-but-unverified to SAFE only once its own
chain evidence was actually compared and agreed, never merely because the
overall crawl came back clean. Corroboration is scoped to what was actually
compared: either a height/tip pair independently reported by at least two
attested (known) operator groups, or genuine agreement with an explicit
trusted reference (`--reference-height` / `--reference-tip-hash`, e.g. your
own Core node; `--reference-checkpoint-hash` / `--reference-genesis-hash` are
optional, stronger evidence for that reference). A configured reference with
no comparable evidence, an endpoint claiming a height nobody actually checked
it against, and a bare height being higher than everyone else's, are all *not*
agreement by themselves: only the specific endpoints whose evidence was
verified are promoted, not every reachable, otherwise-unverified endpoint in
the crawl. A suspected-but-unconfirmed disagreement or a lag never promotes,
and neither does a single self-consistent group by itself.

A disagreement first classifies as suspected; it only escalates to a
confirmed conflict, which demotes the offending group, once a *later*,
independent crawl observes the same group conflicting again. Recovery from a
confirmed conflict requires a positively verified clean comparison on a
subsequent crawl, not merely one crawl where the offending endpoint went
quiet or stopped answering.

## Running it

```sh
python -m network_observer.cli status
python -m network_observer.cli discover-now --policy safe-core-policy.json
python -m network_observer.cli publish --directory-version 3
```

`--policy` is verified, not merely read: the document must carry a valid
Ed25519 signature from the pinned key (`--policy-key`, defaulting to the
production key under `core-safety/production/`) and a `policyVersion` at or
above the highest one this monitor's database has already accepted. A
missing, tampered, wrongly-signed or rolled-back policy is treated the same
as no policy at all: every backend classifies as `UNREVIEWED_CORE`, never
SAFE.

## What the monitor can and cannot say

The monitor is an observability and discovery service. A seed or
`server.peers.subscribe` response supplies candidates; a voluntary registry
adds operators who want to be found. Each candidate is then bounded by depth,
candidate count, concurrency, timeout and response-size limits before health,
TLS, backend and chain evidence are recorded.

Discovery is not trust. A signed directory is a tamper-evident list of
observations, not a permission to skip the wallet's independent validation.
The wallet revalidates every endpoint and fails closed when required evidence
is absent or contradictory.

SSRF protection rejects loopback, private, link-local, unique-local, reserved,
multicast, documentation and cloud-metadata destinations. This matters because
an operator-controlled or malicious peer response must not turn the monitor
into a way to probe internal services.

`operatorGroup` represents an independently reviewed operator, not an
endpoint-count vote. Several endpoints run by CIPIG, ALENOC or another one
operator remain one group; unknown endpoints are not grouped by guesswork,
and are excluded from the independent-operator count entirely, since an
attacker can mint any number of unattested hostnames for free. A healthy
conflicting independent operator is still a conflict.

Observations retain their vantage-point identifier. Thus "unreachable from
probe A" is not overstated as "globally offline"; basic health remains useful
without requiring a distributed consensus service.
