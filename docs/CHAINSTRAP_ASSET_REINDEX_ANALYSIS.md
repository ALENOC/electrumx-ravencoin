# ChainStrap reindex: Ravencoin transfer-deserialization analysis

## Scope

This note records the source-level analysis of the repeated log emitted while
Ravencoin Core v4.8.0 reindexes ChainStrap-provided raw `blk*.dat` files:

```text
ERROR: Failed to get the transfer asset from the stream:
CDataStream::read(): end of data: iostream error
```

The analysis is bound to the certified official Core identity:

- repository: `RavenProject/Ravencoin`
- tag: `v4.8.0`
- commit: `22549129888d02e0e08fcdb9f96f3c699167e774`

No Ravencoin Core behavior is patched or suppressed by this repository.
ChainStrap remains transport-only; Core remains the consensus authority.

## Where the message comes from

`src/assets/assets.cpp::TransferAssetFromScript()` first identifies the asset
payload start, then selects one of two historical parsing rules:

- after `AreTransferScriptsSizeDeployed()` activates, it slices the script from
  the computed `nStartingIndex`;
- before that deployment, it slices from the historical hard-coded offset 31.

The upstream source comment explicitly documents why both paths exist: before
KAWPOW activation the hard-coded offset 31 caused large transfer scripts to
fail to serialize, and the deployment fixes that behavior (upstream issue
RavenProject/Ravencoin#752).

The selected bytes are deserialized through `CDataStream`. A deserialization
exception emits the observed message and returns `false`.

## Consensus path versus raw-file loading path

A failed transfer parse is normally consensus-significant.
`src/consensus/tx_verify.cpp::CheckTransaction()` rejects an asset-transfer
output whose `TransferAssetFromScript()` call fails with:

```text
bad-txns-transfer-asset-bad-deserialize
```

However, Ravencoin has an explicit historical compatibility exception while
**loading existing raw block files**. In
`src/validation.cpp::AcceptBlock(..., fFromLoad=true)`, when `CheckBlock()`
returns exactly `bad-txns-transfer-asset-bad-deserialize`, Core clears that
loader validation state and continues, with the source comment:

```text
keep going, we are only loading blocks from database
```

`LoadExternalBlockFile()` is the reindex/raw-`blk*.dat` reader and calls
`AcceptBlock(..., fFromLoad=true)`. It also only activates the genesis block
while scanning the external file; the loaded block records are later connected
through normal best-chain activation.

This distinction explains why the transfer-deserialization log can appear
while raw records are being discovered/indexed without immediately aborting the
whole `-reindex` process.

## Historical deployment interaction

`AreTransferScriptsSizeDeployed()` derives its state from the active-chain
version-bits state. During `LoadExternalBlockFile()`, raw records can be read
while the active chain has not yet advanced to those records; the loader's
explicit `fFromLoad` exception exists for the historical transfer-script
serialization transition.

The source therefore supports the following mechanism for the observed logs:
raw-file scanning can attempt the historical offset-31 interpretation before
the active chain has advanced enough for the deployed parser, producing the
known deserialization failure which the `fFromLoad` path explicitly tolerates.
Without the exact offending transaction hashes from the old run's log we do
not claim that every observed line was produced by one particular historical
block; the conclusion is deliberately limited to the source-proven loader
behavior.

## Why process exit status alone was insufficient

`LoadExternalBlockFile()` can continue past records that are unsuitable for the
active chain. Therefore `ravend -reindex ...` exiting successfully is necessary
but is not, by itself, the trust decision for a ChainStrap bootstrap.

`docker/core/bootstrap-reindex.sh` now performs a second, still-offline Core
startup and requires all of the following before writing
`.chainstrap-reindex-complete`:

1. `getblockcount` equals the snapshot height from
   `.chainstrap-blocks-ready.json`;
2. `getbestblockhash` equals the snapshot block hash;
3. `getblockhash(snapshot_height)` equals that same snapshot block hash;
4. the asset metadata database answers `listassets` and `getassetdata` for a
   real asset present at the snapshot tip;
5. the address-by-asset index answers `listaddressesbyasset` and does not
   report that `-assetindex` is disabled/unusable.

The verification Core remains offline with both `-connect=0` and the Compose
`network_mode: none` isolation inherited by the validation service.

## Security conclusion

The historical log is not treated as harmless merely because reindex proceeds.
Instead, the bootstrap fails closed unless the resulting **active chain** is
exactly the vetted ChainStrap snapshot and the required Ravencoin asset
metadata/index databases answer real read-only queries.

No log suppression, consensus exception, chainstate import, asset-index import,
or trust in ChainStrap-generated database state is introduced. Only raw block
files are transported; all active state is rebuilt and accepted by the
certified Ravencoin Core binary.
