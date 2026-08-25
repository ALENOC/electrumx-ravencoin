# Monitoring components and terminology

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Architecture](architecture.md) · [Network Observer](network-observer.md)

This path is retained for older links that referred to the original
“Electrum monitor.” The released package is now named `network_observer`.

Two independent monitoring systems exist:

| System | Scope | Authority |
|---|---|---|
| **Ravencoin Network Observer** | Discovers and compares public Electrum endpoints across operators and network vantage points | Reports signed evidence only; it is not consensus or release governance |
| **Ravencoin Node Monitor** | Displays local Core, ElectrumX, host, mempool, and network health for one deployment | Operational dashboard only; it does not classify the public Electrum network |

The Network Observer includes endpoint discovery, SSRF/DNS protection,
Chain Quorum 2.0, signed observation bundles, cryptographic operator
declarations, multi-vantage comparison, active asset capability probes, and
height-bound Asset Data Quorum. Its full design and CLI model are documented in
[Network Observer](network-observer.md).

The separately maintained `ravencoin-node-monitor` is optional in the Compose
deployment. Its dashboard binds to `127.0.0.1:8899` by default. Its privileged
bandwidth/connection controller is a distinct opt-in service and remains
disabled unless explicitly selected.

Neither component changes the legacy Electrum protocol or the public
`server.ravencoin_backend` contract.
