# Core certification

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Security model](security-model.md) · [Validation status](validation-status.md)

## Pipeline

The watcher observes both `2miners/Ravencoin` and `RavenProject/Ravencoin`.
Repository names grant permission to test, never permission to trust:

```text
candidate -> exact commit -> reproducible build -> behavioural tests
          -> PASS / FAIL / REVIEW_REQUIRED -> signed safe-Core policy
```

`BUILD_FAILED` means the candidate did not build. `CERTIFICATION_FAILED` means
candidate behavior violated a mandatory property. `REVIEW_REQUIRED` means a
mandatory property was inconclusive. None of these states becomes safe by
version comparison.

## Certified baseline

The current release certification has 12 mandatory release tests: 12 PASS, no
FAIL, no REVIEW_REQUIRED and no mandatory skips. The exact report and signed
policy are under `core-safety/production/`. Profile revision 1 has digest
`1342d079f2eef7ae0803a247d2908c4b031ee4a542b0f837210f92ba36ae27b2`.

Policy v1 is retained as historical evidence. Policy v2 includes the immutable
profile metadata. The detached signature and public key are published beside
the current policy; private signing keys remain outside Git.

## Release versus live validation

Release certification is bounded, deterministic and does not require a full
mainnet sync, ElectrumX database or production wallet. Live node validation is
separate: it checks the actual canonical chain, checkpoint presence,
`transfer_overflow` activation, txindex, assetindex, REST, asset RPC, ElectrumX
historical index, backend evidence and client `SAFE_CORE_VERIFIED`.

The release PASS must not be read as a deployment PASS.
