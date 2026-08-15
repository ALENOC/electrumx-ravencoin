# Architecture

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Security model](security-model.md)

```text
Electrum wallet -- ElectrumX -- private Core RPC/REST -- Ravencoin Core -- P2P
       wallet queries       indexed history       validated chain
```

The bundled Compose stack keeps Core RPC and REST on a private network. Core
has no wallet in the production image. ElectrumX owns the historical database
and exposes Electrum TCP only when the operator enables it.

The backend capability reports sanitized Core identity, network, height,
synchronization, checkpoint and compatibility evidence. It is a contract for
fail-closed selection, not proof that a remote operator runs a particular binary.
Independent chain validation remains authoritative.

For protocol details, see [protocol documentation](protocol.rst),
[RPC interface](rpc-interface.rst), and the existing
[architecture reference](architecture.rst).
