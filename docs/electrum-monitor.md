# Electrum monitor

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
merged. Observations retain a vantage-point identifier so unreachable-from-one-
probe is not overstated as globally offline.

## Running it

```sh
python -m monitor.cli status
python -m monitor.cli discover-now --policy safe-core-policy.json
python -m monitor.cli publish --directory-version 3
```
