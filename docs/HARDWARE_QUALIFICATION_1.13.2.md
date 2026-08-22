# ElectrumX-Ravencoin 1.13.2 — hardware qualification procedure

This procedure is the merge gate for PR3. It is written for an executor running
commands on real Linux hardware. Do not improvise around a failed checkpoint.
Do not merge any PR in the stack until both scenarios are PASS and the maintainer
has reviewed both evidence bundles.

The executor never receives or handles the release/update private key. Signing
is maintainer-only and is performed with `docs/OFFLINE_RELEASE_SIGNING_1.13.2.md`.

## Fixed release facts

The following facts are release policy and must not be changed during
qualification:

- ElectrumX release: `1.13.2`
- artifact revision: `0`
- Ravencoin Core: `4.8.0`
- Ravencoin Core commit: `22549129888d02e0e08fcdb9f96f3c699167e774`
- reviewed ChainStrap floor manifest:
  `contrib/bootstrap/manifests/rvn-mainnet-2026-08-19.json`
- ChainStrap floor source commit:
  `c4ed0750603ea59823cdd21854d7eb75fe365928`
- floor height: `4501329`
- floor block hash:
  `000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48`

The runtime resolver may select a newer official ChainStrap snapshot. A newer
height/hash is therefore not compared to a hard-coded tip. The exact resolved
commit, metadata digest, height and hash printed during the run become the
identity that must be preserved through raw-block staging and Core validation.

## Publication / hand-off ordering

Hardware qualification uses the real production trust path, not a local
qualification bypass.

1. **Before the 1.13.2 candidate is published**, prepare Scenario B as a real,
   fully working 1.13.1 node and capture its baseline evidence.
2. The maintainer signs the exact 1.13.2 candidate offline and runs the mandatory
   `--verify-only` command. The executor is not involved.
3. The maintainer makes those exact verified bytes available through the normal
   GitHub Release asset namespace. Draft-only/local/ephemeral-key assets do not
   qualify this release path.
4. The maintainer gives the executor only:
   - the independently authenticated **new public key**;
   - the expected PR3 source commit represented by `release-provenance.json`;
   - permission to begin the two scenarios.

Because 1.13.1 cannot authenticate the new schema-v2/new-key release, this is a
manual trust-root transition rather than an automatic 1.13.1 update.

## Common rules for both scenarios

Use dedicated qualification hardware. Run the qualification as root (or through
`sudo -H`) so the host-global revision high-water namespace is deterministic.
Docker Engine and Docker Compose v2 must already work.

Do not put any of the following into an evidence bundle:

- Raven RPC passwords;
- `.secrets/` contents;
- private signing keys;
- shell history containing secrets;
- full environment dumps.

Public keys, release manifests, checksums, Docker mount metadata, block hashes,
container/image IDs and high-water state are acceptable evidence.

For each scenario create a fresh evidence directory and capture terminal output:

```sh
set -euo pipefail
export EVIDENCE="$HOME/rvn-1.13.2-qualification/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$EVIDENCE"
exec > >(tee -a "$EVIDENCE/executor.log") 2>&1
printf 'qualification_start=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
uname -a
docker version
docker compose version
df -hT
```

A non-zero exit at a required checkpoint is a **FAIL**. Preserve the evidence
directory and stop that scenario. Do not clean it up and retry until the
maintainer has reviewed the failure.

---

# Scenario A — fresh 1.13.2 ChainStrap installation

## A0. Preconditions

Use a clean host/project namespace with no prior `electrumx-ravencoin` Compose
resources and no prior 1.13.2 high-water state.

Choose explicit paths. The examples below use:

```sh
export INSTALL_ROOT=/opt/electrumx-ravencoin
export STORAGE_ROOT=/srv/electrumx-ravencoin-storage
export RELEASE_BASE=https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download
```

The storage filesystem must have enough free space for the installer bootstrap
check. Do not override or suppress the disk-space check.

Confirm the project namespace is empty:

```sh
test -z "$(docker ps -a --filter label=com.docker.compose.project=electrumx-ravencoin -q)"
test -z "$(docker volume ls --filter label=com.docker.compose.project=electrumx-ravencoin -q)"
test -z "$(docker network ls --filter label=com.docker.compose.project=electrumx-ravencoin -q)"
```

PASS: all three commands exit `0` with no IDs printed.

## A1. Fetch and verify the published installer path

```sh
cd "$EVIDENCE"
curl -fL --proto '=https' --tlsv1.2 -o electrumx-ravencoin-install.py \
  "$RELEASE_BASE/electrumx-ravencoin-install.py"
curl -fL --proto '=https' --tlsv1.2 -o release-manifest.json \
  "$RELEASE_BASE/release-manifest.json"
curl -fL --proto '=https' --tlsv1.2 -o SHA256SUMS \
  "$RELEASE_BASE/SHA256SUMS"
grep '  electrumx-ravencoin-install.py$' SHA256SUMS | sha256sum -c -
```

PASS: checksum output is exactly:

```text
electrumx-ravencoin-install.py: OK
```

Then execute the production verifier without persistent changes:

```sh
sudo -H python3 ./electrumx-ravencoin-install.py \
  --check-only --chainstrap --without-monitor
```

PASS requires exit code `0` and both of these semantic outcomes in the log:

- the signed 1.13.2 release and independent safe-Core policy verify;
- final line includes `check-only complete: no persistent changes were made`.

## A2. Install using ChainStrap

```sh
sudo -H python3 ./electrumx-ravencoin-install.py \
  --install-dir "$INSTALL_ROOT" \
  --storage-root "$STORAGE_ROOT" \
  --chainstrap --without-monitor
```

Do not detach from this command. Preserve the complete live output.

The run is PASS only if all of the following are observed before the installer
returns `0`:

1. A release-floor binding line beginning exactly with:

   ```text
   Release floor manifest: sha256=
   ```

   and containing:

   ```text
   source=chainstrap/chainstrap.github.io@c4ed0750603ea59823cdd21854d7eb75fe365928 height=4501329 hash=000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48
   ```

2. Runtime resolution prints one exact 40-hex ChainStrap source commit, one
   metadata SHA-256, one snapshot height and one snapshot hash.
3. Every advertised part reaches verified/accepted status. No foreign ZIP member,
   duplicate block, unsafe type, size/SHA mismatch or gateway trust-policy error
   occurs.
4. Raw block staging completes with a contiguous block-file set.
5. The Core reindex phase runs with its container network disabled and reaches:

   ```text
   Release-floor ancestry verified at 4501329:000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48.
   ```

6. The installer reports successful offline Core validation and then starts the
   normal node services.
7. The installer exits `0` and reports installation complete.

## A3. Prove the reindex marker is bound to the block marker

```sh
BLOCK_MARKER="$STORAGE_ROOT/ravencoin-data/.chainstrap-blocks-ready.json"
REINDEX_MARKER="$STORAGE_ROOT/ravencoin-data/.chainstrap-reindex-complete"
test -s "$BLOCK_MARKER"
test -s "$REINDEX_MARKER"
test "$(tr -d '\r\n[:space:]' < "$REINDEX_MARKER")" = "$(sha256sum "$BLOCK_MARKER" | awk '{print $1}')"
cp "$BLOCK_MARKER" "$EVIDENCE/chainstrap-blocks-ready.json"
cp "$REINDEX_MARKER" "$EVIDENCE/chainstrap-reindex-complete"
```

PASS: every command exits `0`.

Record and check the load-bearing floor in the completed marker:

```sh
python3 - "$BLOCK_MARKER" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding='utf-8'))
assert m['schema'] == 2
assert m['chain'] == 'RVN' and m['mode'] == 'mainnet'
assert m['release_floor_height'] == 4501329
assert m['release_floor_blockhash'] == '000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48'
assert isinstance(m['source_commit'], str) and len(m['source_commit']) == 40
assert isinstance(m['metadata_sha256'], str) and len(m['metadata_sha256']) == 64
assert m['height'] >= 4501329
print('marker=PASS')
print(f"resolved_source={m['source_commit']}")
print(f"resolved_metadata_sha256={m['metadata_sha256']}")
print(f"resolved_tip={m['height']}:{m['blockhash']}")
PY
```

Expected first line: `marker=PASS`.

## A4. Prove the mandatory Core validation result

```sh
cd "$INSTALL_ROOT"
COMPOSE='docker compose -p electrumx-ravencoin -f compose.yaml -f compose.storage.yaml -f compose.chainstrap.yaml'
$COMPOSE ps -a | tee "$EVIDENCE/compose-ps.txt"
REINDEX_ID="$($COMPOSE ps -aq ravencoin-bootstrap-reindex)"
test -n "$REINDEX_ID"
test "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$REINDEX_ID")" = none
$COMPOSE exec -T ravencoin-core ravend --version | tee "$EVIDENCE/ravend-version.txt"
$COMPOSE exec -T ravencoin-core raven-cli \
  -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf \
  getblockhash 4501329 | tee "$EVIDENCE/floor-hash.txt"
```

PASS requires:

- reindex container network mode is exactly `none`;
- Ravencoin binary output contains `v4.8.0`;
- `floor-hash.txt` contains exactly:
  `000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48`.

Now compare the Core tip to the resolved marker identity:

```sh
python3 - "$BLOCK_MARKER" > "$EVIDENCE/expected-tip.txt" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding='utf-8'))
print(m['height'])
print(m['blockhash'])
PY
EXPECTED_HEIGHT="$(sed -n '1p' "$EVIDENCE/expected-tip.txt")"
EXPECTED_HASH="$(sed -n '2p' "$EVIDENCE/expected-tip.txt")"
OBSERVED_HEIGHT="$($COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockcount | tr -d '\r\n[:space:]')"
OBSERVED_HASH="$($COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getbestblockhash | tr -d '\r\n[:space:]')"
test "$OBSERVED_HEIGHT" -ge "$EXPECTED_HEIGHT"
if [ "$OBSERVED_HEIGHT" = "$EXPECTED_HEIGHT" ]; then test "$OBSERVED_HASH" = "$EXPECTED_HASH"; fi
$COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockhash "$EXPECTED_HEIGHT" | tr -d '\r\n[:space:]' | grep -Fx "$EXPECTED_HASH"
```

The normal networked node may advance beyond the snapshot after the offline
reindex. PASS therefore requires either an identical current tip or, if it has
advanced, the exact resolved snapshot hash must still exist at its resolved
height.

## A5. Prove ElectrumX is serving the same Core chain

Poll, do not force restart:

```sh
for i in $(seq 1 120); do
  CORE_HEIGHT="$($COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockcount 2>/dev/null | tr -d '\r\n[:space:]' || true)"
  INFO="$($COMPOSE exec -T electrumx electrumx_rpc getinfo 2>/dev/null || true)"
  if [ -n "$CORE_HEIGHT" ] && [ -n "$INFO" ] && python3 - "$CORE_HEIGHT" "$INFO" <<'PY'
import json, sys
h=int(sys.argv[1]); info=json.loads(sys.argv[2])
assert str(info.get('version','')).endswith('1.13.2')
assert info.get('db height') == h
assert info.get('daemon height') == h
PY
  then
    printf '%s\n' "$INFO" | tee "$EVIDENCE/electrumx-getinfo.json"
    break
  fi
  sleep 15
  test "$i" -lt 120
 done
```

PASS: loop exits through the success branch and stores
`electrumx-getinfo.json` with version ending in `1.13.2`, DB height equal to
Core height and daemon height equal to Core height.

## A6. Prove completed bootstrap immutability

Re-run only the completed one-shot bootstrap service against the existing data
volume:

```sh
$COMPOSE run --rm --no-deps chainstrap-bootstrap 2>&1 | tee "$EVIDENCE/chainstrap-rerun.txt"
grep -F 'runtime upstream resolution is intentionally skipped.' "$EVIDENCE/chainstrap-rerun.txt"
```

PASS: exit code `0` and the exact skip phrase is present. This proves a completed
bootstrap does not re-resolve mutable upstream metadata merely because a newer
snapshot may exist.

## A7. Prove host-global revision high-water

```sh
sudo stat -c '%U:%G %a %n' \
  /var/lib/electrumx-ravencoin/security-state.locator \
  /var/lib/electrumx-ravencoin/security-state.json \
  | tee "$EVIDENCE/high-water-stat.txt"
sudo python3 - <<'PY' | tee "$EVIDENCE/high-water.json"
import json
p='/var/lib/electrumx-ravencoin/security-state.json'
s=json.load(open(p, encoding='utf-8'))
assert s['highestAcceptedVersion'] == '1.13.2'
r=s['releases']['1.13.2']
assert r['artifact_revision'] == 0
print(json.dumps(s, indent=2, sort_keys=True))
PY
```

PASS requires locator ownership/mode `root:root 644`, state ownership/mode
`root:root 600`, highest version `1.13.2`, revision `0`.

### Scenario A PASS

Scenario A is PASS only when A0 through A7 all pass. Copy the downloaded
`release-manifest.json` and `SHA256SUMS` into the evidence directory and record:

```sh
printf 'scenario=A\nresult=PASS\nfinished=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  | tee "$EVIDENCE/RESULT.txt"
```

---

# Scenario B — real 1.13.1 to 1.13.2 manual trust transition + updater apply

Scenario B must start from a real working 1.13.1 installation created **before**
the 1.13.2 qualification release is published. Do not emulate the old release
with a rebuilt source tree.

The purpose is to prove two things simultaneously:

1. the old release cannot silently grant trust to the new key; the public-key
   transition is explicit and out-of-band;
2. after that manual trust reset, the 1.13.2 updater performs the actual
   transactional software update while preserving the four bind-backed data
   locations and without running ChainStrap again.

The examples assume the existing install root is `/opt/electrumx-ravencoin`.
Adjust only `INSTALL_ROOT` if the real 1.13.1 node uses another location.

## B0. Capture the live 1.13.1 baseline before candidate publication

On the working 1.13.1 host:

```sh
export INSTALL_ROOT=/opt/electrumx-ravencoin
cd "$INSTALL_ROOT"
BASE_COMPOSE='docker compose -p electrumx-ravencoin -f compose.yaml -f compose.storage.yaml'
$BASE_COMPOSE ps | tee "$EVIDENCE/before-compose-ps.txt"
$BASE_COMPOSE exec -T ravencoin-core ravend --version | tee "$EVIDENCE/before-ravend-version.txt"
$BASE_COMPOSE exec -T electrumx electrumx_rpc getinfo | tee "$EVIDENCE/before-electrumx-getinfo.json"
BEFORE_HEIGHT="$($BASE_COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockcount | tr -d '\r\n[:space:]')"
BEFORE_HASH="$($BASE_COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockhash "$BEFORE_HEIGHT" | tr -d '\r\n[:space:]')"
printf '%s %s\n' "$BEFORE_HEIGHT" "$BEFORE_HASH" | tee "$EVIDENCE/before-tip.txt"
```

PASS precondition: ElectrumX reports 1.13.1, Core is healthy mainnet, and a
concrete height/hash pair is captured.

Capture only the four storage-path variables, never the whole environment:

```sh
grep -E '^(RAVENCOIN_DATA_HOST_DIR|RAVENCOIN_CONFIG_HOST_DIR|ELECTRUMX_DATA_HOST_DIR|MONITOR_DATA_HOST_DIR)=' .env \
  | sort | tee "$EVIDENCE/before-storage.env"
for v in ravencoin-data ravencoin-config electrumx-data monitor-data; do
  docker volume inspect "electrumx-ravencoin_${v}" > "$EVIDENCE/before-volume-${v}.json"
done
python3 - "$EVIDENCE/before-storage.env" <<'PY' | tee "$EVIDENCE/before-storage-stat.txt"
import os, sys
values={}
for line in open(sys.argv[1], encoding='utf-8'):
    k,v=line.rstrip('\n').split('=',1); values[k]=v
for key in sorted(values):
    st=os.stat(values[key], follow_symlinks=False)
    assert os.path.isdir(values[key]) and not os.path.islink(values[key])
    print(f'{key}={values[key]} device={st.st_dev} inode={st.st_ino}')
PY
```

Also preserve the old **public** update key and any legacy updater state for
review; neither is secret:

```sh
cp core-safety/production/update-signing-public-key.hex \
  "$EVIDENCE/before-update-signing-public-key.hex"
STATE_DIR="$(dirname "$INSTALL_ROOT")/.$(basename "$INSTALL_ROOT").state"
if [ -d "$STATE_DIR" ]; then sudo cp -a "$STATE_DIR" "$EVIDENCE/legacy-updater-state"; fi
```

Do not continue with B1 until the maintainer has completed offline signing,
verification and publication of the exact candidate.

## B1. Authenticate the new trust root out of band and verify the release

The executor receives `<NEW_PUBLIC_KEY_HEX>` from the maintainer through the
agreed independent channel. It is public information but **must not be learned
from the release being authenticated**.

```sh
export NEW_PUBLIC_KEY_HEX='<NEW_PUBLIC_KEY_HEX>'
export RELEASE_BASE=https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download
export TRANSITION="$EVIDENCE/candidate-tree"
cd "$EVIDENCE"
curl -fL --proto '=https' --tlsv1.2 -o candidate-installer.py \
  "$RELEASE_BASE/electrumx-ravencoin-install.py"
curl -fL --proto '=https' --tlsv1.2 -o release-manifest.json \
  "$RELEASE_BASE/release-manifest.json"
curl -fL --proto '=https' --tlsv1.2 -o release-provenance.json \
  "$RELEASE_BASE/release-provenance.json"
curl -fL --proto '=https' --tlsv1.2 -o electrumx-ravencoin-bundle.tar.gz \
  "$RELEASE_BASE/electrumx-ravencoin-bundle.tar.gz"
```

First bind the downloaded standalone installer to the independently supplied
public key without executing it:

```sh
python3 - "$NEW_PUBLIC_KEY_HEX" candidate-installer.py <<'PY'
import re, sys
expected=sys.argv[1]
text=open(sys.argv[2], encoding='utf-8').read()
m=re.search(r'^RELEASE_PUBLIC_KEY_HEX = "([0-9a-f]{64})"$', text, re.M)
assert m and m.group(1) == expected
print('out_of_band_public_key_binding=PASS')
PY
```

Expected output: `out_of_band_public_key_binding=PASS`.

Now run the production check-only path:

```sh
sudo -H python3 ./candidate-installer.py --check-only --chainstrap --without-monitor
```

PASS: exit `0`, signed release verified, safe-Core policy verified, and final
`check-only complete: no persistent changes were made`.

Verify the separately downloaded bundle against the now-authenticated signed
manifest:

```sh
EXPECTED_BUNDLE_SHA="$(python3 -c 'import json; print(json.load(open("release-manifest.json"))["manifest"]["artifactDigest"].split(":",1)[1])')"
printf '%s  %s\n' "$EXPECTED_BUNDLE_SHA" electrumx-ravencoin-bundle.tar.gz | sha256sum -c -
mkdir -p "$TRANSITION"
tar -xzf electrumx-ravencoin-bundle.tar.gz -C "$TRANSITION"
```

Expected checksum output:

```text
electrumx-ravencoin-bundle.tar.gz: OK
```

Require the provenance source commit to equal the exact PR3 head supplied by the
maintainer for qualification:

```sh
python3 - release-provenance.json '<EXPECTED_PR3_HEAD_SHA>' <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding='utf-8'))
assert p['electrumxVersion'] == '1.13.2'
assert p['artifact_revision'] == 0
assert p['sourceCommit'] == sys.argv[2]
print('candidate_source_identity=PASS')
PY
```

Expected output: `candidate_source_identity=PASS`.

## B2. Perform the explicit public trust-root transition

This is the one manual trust reset. It is intentionally not signed by or
approved through the retired 1.13.1 key.

```sh
printf '%s\n' "$NEW_PUBLIC_KEY_HEX" \
  | sudo tee "$INSTALL_ROOT/core-safety/production/update-signing-public-key.hex" >/dev/null
sudo chmod 0644 "$INSTALL_ROOT/core-safety/production/update-signing-public-key.hex"
test "$(tr -d '\r\n[:space:]' < "$INSTALL_ROOT/core-safety/production/update-signing-public-key.hex")" = "$NEW_PUBLIC_KEY_HEX"
```

PASS: all commands exit `0`. No private key is present on the host.

The legacy updater state cannot authenticate revision-aware v2 identity and is
therefore deliberately archived, not migrated. Initialize a clean operational
v3 state at the normal state path while leaving blockchain/ElectrumX data
untouched:

```sh
sudo -H env ELECTRUMX_INSTALL_ROOT="$INSTALL_ROOT" PYTHONPATH="$TRANSITION/core-safety/scripts" \
python3 - <<'PY'
from pathlib import Path
import electrumx_update_cli as cli
from update_state import UpdateState, save_state
p=Path(cli.DEFAULT_STATE_PATH)
p.parent.mkdir(parents=True, exist_ok=True)
save_state(str(p), UpdateState(failure_reason='manual 1.13.1 -> 1.13.2 trust-root transition'))
print(f'v3_state={p}')
PY
```

Provision the root-owned host-global high-water locator using the candidate code:

```sh
sudo -H env PYTHONPATH="$TRANSITION/core-safety/scripts" python3 - <<'PY'
from electrumx_core_safety import artifact_revision
p=artifact_revision.resolve_host_high_water_path(provision_root_locator=True)
print(f'high_water={p}')
PY
```

Expected high-water path:
`/var/lib/electrumx-ravencoin/security-state.json`.

## B3. Run the real v2 updater check

Execute the updater **from the signed candidate tree**, but point it at the real
1.13.1 installation:

```sh
sudo -H env \
  ELECTRUMX_INSTALL_ROOT="$INSTALL_ROOT" \
  PYTHONPATH="$TRANSITION/core-safety/scripts" \
  python3 "$TRANSITION/core-safety/scripts/electrumx_update_cli.py" check \
  | tee "$EVIDENCE/update-check.txt"
```

PASS requires exit `0` and a pending 1.13.2 candidate that verified under the
new key and safe-Core policy. Confirm from the persisted v3 state rather than
relying only on display text:

```sh
sudo -H env ELECTRUMX_INSTALL_ROOT="$INSTALL_ROOT" PYTHONPATH="$TRANSITION/core-safety/scripts" \
python3 - <<'PY'
import electrumx_update_cli as cli
from update_state import load_state
s=load_state(cli.DEFAULT_STATE_PATH)
p=s.pending_candidate
assert p is not None
assert p['manifest']['electrumxVersion'] == '1.13.2'
assert p['manifest']['artifact_revision'] == 0
assert p['_verificationVerdict'] == 'VERIFIED'
print('pending_candidate=VERIFIED')
PY
```

Expected output: `pending_candidate=VERIFIED`.

## B4. Apply through the transactional updater

```sh
sudo -H env \
  ELECTRUMX_INSTALL_ROOT="$INSTALL_ROOT" \
  PYTHONPATH="$TRANSITION/core-safety/scripts" \
  python3 "$TRANSITION/core-safety/scripts/electrumx_update_cli.py" apply \
  | tee "$EVIDENCE/update-apply.txt"
```

PASS requires exit `0` and promotion to current. The updater itself must:

- prove all four existing host bind paths before stopping the old node;
- prove Docker volume objects point to those paths;
- prove active old containers are mounted on those exact volume objects;
- stage/build 1.13.2 while the old node is still running;
- copy only the allowlisted operator state;
- prove the candidate Compose model resolves to the same four host paths;
- stop the old stack;
- atomically switch the release directory;
- re-prove storage before starting the new stack;
- start 1.13.2 without running the ChainStrap one-shot again;
- pass Core/ElectrumX health gates;
- advance the host-global high-water only after promotion.

Any updater rollback or STUCK verdict is Scenario B **FAIL**, even if the old node
is successfully restored.

## B5. Prove data/storage continuity after apply

From the now-updated install root:

```sh
cd "$INSTALL_ROOT"
grep -E '^(RAVENCOIN_DATA_HOST_DIR|RAVENCOIN_CONFIG_HOST_DIR|ELECTRUMX_DATA_HOST_DIR|MONITOR_DATA_HOST_DIR)=' .env \
  | sort | tee "$EVIDENCE/after-storage.env"
cmp -s "$EVIDENCE/before-storage.env" "$EVIDENCE/after-storage.env"
for v in ravencoin-data ravencoin-config electrumx-data monitor-data; do
  docker volume inspect "electrumx-ravencoin_${v}" > "$EVIDENCE/after-volume-${v}.json"
done
python3 - "$EVIDENCE/after-storage.env" <<'PY' | tee "$EVIDENCE/after-storage-stat.txt"
import os, sys
values={}
for line in open(sys.argv[1], encoding='utf-8'):
    k,v=line.rstrip('\n').split('=',1); values[k]=v
for key in sorted(values):
    st=os.stat(values[key], follow_symlinks=False)
    assert os.path.isdir(values[key]) and not os.path.islink(values[key])
    print(f'{key}={values[key]} device={st.st_dev} inode={st.st_ino}')
PY
cmp -s "$EVIDENCE/before-storage-stat.txt" "$EVIDENCE/after-storage-stat.txt"
```

PASS: exact host paths and directory device/inode identities are unchanged.

Prove Docker volume `device` values are unchanged:

```sh
python3 - "$EVIDENCE" <<'PY'
import json, pathlib, sys
root=pathlib.Path(sys.argv[1])
for name in ('ravencoin-data','ravencoin-config','electrumx-data','monitor-data'):
    before=json.load(open(root/f'before-volume-{name}.json', encoding='utf-8'))[0]
    after=json.load(open(root/f'after-volume-{name}.json', encoding='utf-8'))[0]
    assert before['Driver'] == after['Driver'] == 'local'
    assert before['Options'] == after['Options']
    assert before['Options'].get('type') == 'none'
    assert 'bind' in {x.strip() for x in before['Options'].get('o','').split(',')}
print('docker_volume_bindings=UNCHANGED')
PY
```

Expected output: `docker_volume_bindings=UNCHANGED`.

## B6. Prove chain continuity and 1.13.2 service identity

```sh
POST_COMPOSE='docker compose -p electrumx-ravencoin -f compose.yaml -f compose.storage.yaml'
$POST_COMPOSE ps | tee "$EVIDENCE/after-compose-ps.txt"
$POST_COMPOSE exec -T ravencoin-core ravend --version | tee "$EVIDENCE/after-ravend-version.txt"
$POST_COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockhash "$BEFORE_HEIGHT" \
  | tr -d '\r\n[:space:]' | grep -Fx "$BEFORE_HASH"
```

PASS: Core remains 4.8.0 and the exact pre-upgrade tip block is still present at
its original height.

Poll ElectrumX until it catches the live Core tip:

```sh
for i in $(seq 1 120); do
  CORE_HEIGHT="$($POST_COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockcount 2>/dev/null | tr -d '\r\n[:space:]' || true)"
  INFO="$($POST_COMPOSE exec -T electrumx electrumx_rpc getinfo 2>/dev/null || true)"
  if [ -n "$CORE_HEIGHT" ] && [ -n "$INFO" ] && python3 - "$CORE_HEIGHT" "$INFO" <<'PY'
import json, sys
h=int(sys.argv[1]); info=json.loads(sys.argv[2])
assert str(info.get('version','')).endswith('1.13.2')
assert info.get('db height') == h
assert info.get('daemon height') == h
PY
  then
    printf '%s\n' "$INFO" | tee "$EVIDENCE/after-electrumx-getinfo.json"
    break
  fi
  sleep 15
  test "$i" -lt 120
 done
```

PASS: version ends in 1.13.2 and ElectrumX DB/daemon heights equal Core.

## B7. Prove ChainStrap did not run during the software update

If the 1.13.1 baseline had a historical ChainStrap one-shot container, its ID
must not be replaced by the updater. If the baseline used P2P, no new
`chainstrap-bootstrap` container may appear.

Record the post-update set:

```sh
docker ps -a --filter name=chainstrap-bootstrap --format '{{.ID}} {{.CreatedAt}} {{.Status}}' \
  | tee "$EVIDENCE/after-chainstrap-containers.txt"
```

Compare this with a pre-update capture if the baseline had ChainStrap. PASS is
no newly created ChainStrap bootstrap execution attributable to B4.

## B8. Prove the new trust root and high-water are installed

```sh
test "$(tr -d '\r\n[:space:]' < "$INSTALL_ROOT/core-safety/production/update-signing-public-key.hex")" = "$NEW_PUBLIC_KEY_HEX"
sudo stat -c '%U:%G %a %n' \
  /var/lib/electrumx-ravencoin/security-state.locator \
  /var/lib/electrumx-ravencoin/security-state.json \
  | tee "$EVIDENCE/after-high-water-stat.txt"
sudo python3 - <<'PY' | tee "$EVIDENCE/after-high-water.json"
import json
s=json.load(open('/var/lib/electrumx-ravencoin/security-state.json', encoding='utf-8'))
assert s['highestAcceptedVersion'] == '1.13.2'
r=s['releases']['1.13.2']
assert r['artifact_revision'] == 0
print(json.dumps(s, indent=2, sort_keys=True))
PY
```

PASS requires the installed public key to equal the out-of-band key, locator
`root:root 644`, state `root:root 600`, highest version 1.13.2 and revision 0.

### Scenario B PASS

Scenario B is PASS only when B0 through B8 all pass. Record:

```sh
printf 'scenario=B\nresult=PASS\nfinished=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  | tee "$EVIDENCE/RESULT.txt"
```

---

# Evidence return contract

For each scenario return one compressed evidence directory containing the files
created above plus the complete `executor.log`. Do **not** include secrets.

The executor's final report must contain only:

```text
Scenario A: PASS|FAIL
Scenario B: PASS|FAIL
A evidence: <path/archive>
B evidence: <path/archive>
Qualified release: 1.13.2 artifact_revision=0
Qualified source commit: <release-provenance sourceCommit>
Resolved ChainStrap snapshot in A: <sourceCommit> <metadata_sha256> <height> <blockhash>
Failure checkpoint: <none or exact A#/B# checkpoint>
```

A PASS is not inferred from a generally healthy node. Every checkpoint above is
part of the release evidence. If either scenario is FAIL, PR3 remains unmerged.
