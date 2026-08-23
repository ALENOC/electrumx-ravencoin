# ElectrumX-Ravencoin 1.13.3 — hardware qualification procedure

This procedure is the merge gate for PR3. It is written for an executor running
commands on real Linux hardware. Do not improvise around a failed checkpoint.
Do not merge any PR in the stack until both scenarios are PASS and the maintainer
has reviewed both evidence bundles.

The executor never receives or handles the release/update private key. Signing
is maintainer-only and is performed with `docs/OFFLINE_RELEASE_SIGNING_1.13.3.md`.

## Fixed release facts

The following facts are release policy and must not be changed during
qualification:

- ElectrumX release: `1.13.3`
- qualification release tag: `v1.13.3`
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
Scenario A intentionally uses the default `runtime-master` path: it must resolve
current official upstream at run time and the resolved snapshot height must be
strictly greater than the reviewed release floor.

## Publication / hand-off ordering

Hardware qualification uses the real production trust path, not a local
qualification bypass.

1. **Before the 1.13.3 candidate is published**, prepare Scenario B as a real,
   fully working 1.13.1 node and capture its baseline evidence and B0.5 rollback
   checkpoint.
2. The maintainer signs the exact 1.13.3 candidate offline and runs the mandatory
   `--verify-only` command. The executor is not involved.
3. The maintainer makes those exact verified bytes available under the exact
   GitHub Release tag `v1.13.3`. `releases/latest/download` is never used for
   qualification.
4. The maintainer gives the executor only:
   - the independently authenticated **new public key**;
   - the expected PR3 source commit represented by `release-provenance.json`;
   - permission to begin the two scenarios.

Because 1.13.1 cannot authenticate the new schema-v2/new-key release, this is a
manual trust-root transition rather than an automatic 1.13.1 update.

## Prior 1.13.2 candidate: qualification result carried forward

The withdrawn 1.13.2 candidate was executed against this exact procedure on real
hardware. Scenario B failed at B4 (staged apply) with:

```text
stat .../compose.local-core-identity.yaml: no such file or directory
```

The mandatory abort/restore path was then exercised for real and passed:

```text
UPDATER_CHECKPOINT legacy-adoption-rollback=PASS marker=REMOVED old-stack=PRESERVED
```

Two results from that run are carried forward as verified evidence and are not
re-derived by this document: the rollback/restore path is proven on real
hardware, and the legacy adoption marker is proven to be removed on a failed
pre-promotion attempt without touching the old 1.13.1 stack.

Everything else must be re-executed. 1.13.3 changes executable updater behavior
(explicit resolution of staged Compose files), so the B3-B6 checkpoints from the
1.13.2 run carry no qualification value. The GitHub Release for 1.13.2 was
deleted; the tag `v1.13.2` is retained only as a historical trace and must never
be used as a qualification target.

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
container/image IDs and high-water state are acceptable evidence. The B0.5
rollback copy may contain secrets because it is a local recovery backup; it is
**not** part of the evidence bundle and must not be handed back.

For each scenario create a fresh evidence directory and capture terminal output:

```sh
set -euo pipefail
export EVIDENCE="$HOME/rvn-1.13.3-qualification/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$EVIDENCE"
exec > >(tee -a "$EVIDENCE/executor.log") 2>&1
printf 'qualification_start=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
uname -a
docker version
docker compose version
df -hT
```

A non-zero exit at a required checkpoint is a **FAIL**. Preserve the evidence
directory and stop normal qualification progress. For Scenario B failures at
B3 through B6, run the mandatory abort-and-restore procedure before returning
the evidence. Do not clean up and retry until the maintainer has reviewed the
failure.

---

# Scenario A — fresh 1.13.3 ChainStrap installation

## A0. Preconditions

Use a clean host/project namespace with no prior `electrumx-ravencoin` Compose
resources and no prior 1.13.3 high-water state.

Choose explicit paths. The examples below use:

```sh
export INSTALL_ROOT=/opt/electrumx-ravencoin
export STORAGE_ROOT=/srv/electrumx-ravencoin-storage
export RELEASE_TAG=v1.13.3
export RELEASE_BASE="https://github.com/ALENOC/electrumx-ravencoin/releases/download/$RELEASE_TAG"
```

The storage filesystem must have enough free space for the installer bootstrap
check. Do not override or suppress the disk-space check.

Qualification is pinned to `v1.13.3`. If that exact tag or any required asset is
absent, `curl -f` in A1 fails and Scenario A stops. If GitHub's `latest` release
points to another tag, that has no effect because `latest` is never consulted.

Confirm the project namespace is empty:

```sh
test -z "$(docker ps -a --filter label=com.docker.compose.project=electrumx-ravencoin -q)"
test -z "$(docker volume ls --filter label=com.docker.compose.project=electrumx-ravencoin -q)"
test -z "$(docker network ls --filter label=com.docker.compose.project=electrumx-ravencoin -q)"
```

PASS: all three commands exit `0` with no IDs printed.

## A1. Fetch and verify the exact tagged installer path

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

- the signed 1.13.3 release and independent safe-Core policy verify;
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

Record and check the load-bearing floor and the live runtime-master resolution in
the completed marker:

```sh
python3 - "$BLOCK_MARKER" <<'PY'
import json, sys
m=json.load(open(sys.argv[1], encoding='utf-8'))
assert m['schema'] == 2
assert m['chain'] == 'RVN' and m['mode'] == 'mainnet'
assert m['resolution_mode'] == 'runtime-master'
assert m['release_floor_height'] == 4501329
assert m['release_floor_blockhash'] == '000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48'
assert isinstance(m['source_commit'], str) and len(m['source_commit']) == 40
assert isinstance(m['metadata_sha256'], str) and len(m['metadata_sha256']) == 64
assert m['height'] > 4501329
print('marker=PASS')
print(f"resolved_source={m['source_commit']}")
print(f"resolved_metadata_sha256={m['metadata_sha256']}")
print(f"resolved_tip={m['height']}:{m['blockhash']}")
PY
```

Expected first line: `marker=PASS`. Any `reviewed-local`/`exact-commit` marker or
resolved height equal to the floor is Scenario A FAIL: this scenario must exercise
mutable-master resolution against a snapshot newer than the release floor.

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
assert str(info.get('version','')).endswith('1.13.3')
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
`electrumx-getinfo.json` with version ending in `1.13.3`, DB height equal to
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
assert s['highestAcceptedVersion'] == '1.13.3'
r=s['releases']['1.13.3']
assert r['artifact_revision'] == 0
print(json.dumps(s, indent=2, sort_keys=True))
PY
```

PASS requires locator ownership/mode `root:root 644`, state ownership/mode
`root:root 600`, highest version `1.13.3`, revision `0`.

### Scenario A PASS

Scenario A is PASS only when A0 through A7 all pass. Copy the downloaded
`release-manifest.json` and `SHA256SUMS` into the evidence directory and record:

```sh
printf 'scenario=A\nresult=PASS\nfinished=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  | tee "$EVIDENCE/RESULT.txt"
```

---

# Scenario B — real 1.13.1 to 1.13.3 manual trust transition + updater apply

Scenario B must start from a real working 1.13.1 installation created **before**
the 1.13.3 qualification release is published. Do not emulate the old release
with a rebuilt source tree.

The purpose is to prove two things simultaneously:

1. the old release cannot silently grant trust to the new key; the public-key
   transition is explicit and out-of-band;
2. after that manual trust reset, the 1.13.3 updater performs the actual
   transactional software update while preserving the four bind-backed data
   locations and without running ChainStrap again.

The examples assume the existing install root is `/opt/electrumx-ravencoin`.
Adjust only `INSTALL_ROOT` if the real 1.13.1 node uses another location.

Before starting B0, confirm the host anti-rollback high-water still reflects
1.13.1 and was never advanced by the withdrawn 1.13.2 attempt:

```bash
sudo cat /var/lib/electrumx-ravencoin/security-state.json 2>/dev/null || \
  echo "ABSENT (expected on a 1.13.1-only host)"
```

The file must be absent, or must record `highestAcceptedVersion` `1.13.1`. A
recorded `1.13.2` means an earlier attempt advanced further than its checkpoints
reported. Because 1.13.3 is greater than 1.13.2 the updater would accept it
silently, so this host is not a valid Scenario B starting point: rebuild the
1.13.1 node before continuing.

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

## B0.5. Create and verify the mandatory rollback checkpoint

**[ELEVATED] [SERVICE INTERRUPTION]** Qualification does not start until this
checkpoint exists and has been verified. A filesystem-native snapshot is
acceptable if it captures the same objects below and its snapshot ID/readback
verification is recorded. The portable procedure below instead makes a verified
`rsync` copy while the 1.13.1 services are stopped.

The rollback checkpoint must capture enough to restore the exact
pre-qualification disk/control state:

- the complete 1.13.1 `INSTALL_ROOT`, including `.env`, certificates, secrets,
  install marker and old public trust root;
- all four bind-backed host directories from `before-storage.env`;
- the complete updater state directory if it existed before qualification, or
  an explicit record that it was absent;
- `/var/lib/electrumx-ravencoin` if it existed before qualification, or an
  explicit record that it was absent;
- a concrete Core height/hash captured immediately before stopping the node;
- the original four Docker volume definitions already saved by B0.

The rollback copy is local recovery material, not evidence. Do not upload it.

```sh
command -v rsync >/dev/null
export BACKUP_ROOT="/srv/electrumx-ravencoin-qualification-backup/$(date -u +%Y%m%dT%H%M%SZ)"
printf '%s\n' "$BACKUP_ROOT" | tee "$EVIDENCE/backup-root.txt"
sudo install -d -m 0700 "$BACKUP_ROOT" "$BACKUP_ROOT/data"

mapfile -t STORAGE_PATHS < <(python3 - "$EVIDENCE/before-storage.env" <<'PY'
import sys
keys=(
 'RAVENCOIN_DATA_HOST_DIR','RAVENCOIN_CONFIG_HOST_DIR',
 'ELECTRUMX_DATA_HOST_DIR','MONITOR_DATA_HOST_DIR')
values={}
for line in open(sys.argv[1], encoding='utf-8'):
    k,v=line.rstrip('\n').split('=',1); values[k]=v
assert set(values) == set(keys)
for k in keys:
    print(values[k])
PY
)
test "${#STORAGE_PATHS[@]}" -eq 4
STORAGE_NAMES=(ravencoin-data ravencoin-config electrumx-data monitor-data)
for p in "${STORAGE_PATHS[@]}"; do test -d "$p" && test ! -L "$p"; done

STATE_DIR="$(dirname "$INSTALL_ROOT")/.$(basename "$INSTALL_ROOT").state"
HIGH_WATER_DIR=/var/lib/electrumx-ravencoin
RESTORE_HEIGHT="$($BASE_COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockcount | tr -d '\r\n[:space:]')"
RESTORE_HASH="$($BASE_COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockhash "$RESTORE_HEIGHT" | tr -d '\r\n[:space:]')"
printf '%s %s\n' "$RESTORE_HEIGHT" "$RESTORE_HASH" | tee "$EVIDENCE/restore-tip.txt"

$BASE_COMPOSE stop
sudo rsync -aHAX --numeric-ids --delete "$INSTALL_ROOT/" "$BACKUP_ROOT/install-root/"
for i in 0 1 2 3; do
  sudo install -d -m 0700 "$BACKUP_ROOT/data/${STORAGE_NAMES[$i]}"
  sudo rsync -aHAX --numeric-ids --delete \
    "${STORAGE_PATHS[$i]}/" "$BACKUP_ROOT/data/${STORAGE_NAMES[$i]}/"
done

if sudo test -d "$STATE_DIR"; then
  printf 'STATE_DIR=present\n' | tee "$EVIDENCE/prequal-control-presence.txt"
  sudo rsync -aHAX --numeric-ids --delete "$STATE_DIR/" "$BACKUP_ROOT/updater-state/"
else
  printf 'STATE_DIR=absent\n' | tee "$EVIDENCE/prequal-control-presence.txt"
fi
if sudo test -d "$HIGH_WATER_DIR"; then
  printf 'HIGH_WATER_DIR=present\n' | tee -a "$EVIDENCE/prequal-control-presence.txt"
  sudo rsync -aHAX --numeric-ids --delete "$HIGH_WATER_DIR/" "$BACKUP_ROOT/high-water/"
else
  printf 'HIGH_WATER_DIR=absent\n' | tee -a "$EVIDENCE/prequal-control-presence.txt"
fi

sudo rsync -aHAXn --numeric-ids --delete --itemize-changes \
  "$INSTALL_ROOT/" "$BACKUP_ROOT/install-root/" | tee "$EVIDENCE/verify-backup-install-root.txt"
test ! -s "$EVIDENCE/verify-backup-install-root.txt"
for i in 0 1 2 3; do
  sudo rsync -aHAXn --numeric-ids --delete --itemize-changes \
    "${STORAGE_PATHS[$i]}/" "$BACKUP_ROOT/data/${STORAGE_NAMES[$i]}/" \
    | tee "$EVIDENCE/verify-backup-${STORAGE_NAMES[$i]}.txt"
  test ! -s "$EVIDENCE/verify-backup-${STORAGE_NAMES[$i]}.txt"
done
if grep -Fxq 'STATE_DIR=present' "$EVIDENCE/prequal-control-presence.txt"; then
  sudo rsync -aHAXn --numeric-ids --delete --itemize-changes \
    "$STATE_DIR/" "$BACKUP_ROOT/updater-state/" | tee "$EVIDENCE/verify-backup-updater-state.txt"
  test ! -s "$EVIDENCE/verify-backup-updater-state.txt"
fi
if grep -Fxq 'HIGH_WATER_DIR=present' "$EVIDENCE/prequal-control-presence.txt"; then
  sudo rsync -aHAXn --numeric-ids --delete --itemize-changes \
    "$HIGH_WATER_DIR/" "$BACKUP_ROOT/high-water/" | tee "$EVIDENCE/verify-backup-high-water.txt"
  test ! -s "$EVIDENCE/verify-backup-high-water.txt"
fi
printf 'backup=VERIFIED\n' | sudo tee "$BACKUP_ROOT/VERIFIED" >/dev/null
```

No source covered by the backup may be running or changing during the copy and
dry-run comparison. Every `verify-backup-*.txt` produced above must be empty.

Restart the unchanged 1.13.1 node and prove it is serving before B1:

```sh
cd "$INSTALL_ROOT"
$BASE_COMPOSE up -d --no-build
for i in $(seq 1 120); do
  CORE_HEIGHT="$($BASE_COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockcount 2>/dev/null | tr -d '\r\n[:space:]' || true)"
  RESTORED_HASH="$($BASE_COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockhash "$RESTORE_HEIGHT" 2>/dev/null | tr -d '\r\n[:space:]' || true)"
  INFO="$($BASE_COMPOSE exec -T electrumx electrumx_rpc getinfo 2>/dev/null || true)"
  if [ "$RESTORED_HASH" = "$RESTORE_HASH" ] && [ -n "$CORE_HEIGHT" ] && [ -n "$INFO" ] && \
     python3 - "$CORE_HEIGHT" "$INFO" <<'PY'
import json, sys
h=int(sys.argv[1]); info=json.loads(sys.argv[2])
assert str(info.get('version','')).endswith('1.13.1')
assert info.get('db height') == h
assert info.get('daemon height') == h
PY
  then
    echo 'prequal_backup_and_restart=PASS'
    break
  fi
  sleep 15
  test "$i" -lt 120
done
sudo test "$(cat "$BACKUP_ROOT/VERIFIED")" = 'backup=VERIFIED'
```

Expected output includes exactly `prequal_backup_and_restart=PASS`. Do not
continue to B1 without it and the `VERIFIED` marker.

Do not continue with B1 until the maintainer has completed offline signing,
verification and publication of the exact candidate under tag `v1.13.3`.

## B1. Authenticate the new trust root out of band and verify the exact tagged release

The executor receives `<NEW_PUBLIC_KEY_HEX>` from the maintainer through the
agreed independent channel. It is public information but **must not be learned
from the release being authenticated**.

```sh
export NEW_PUBLIC_KEY_HEX='<NEW_PUBLIC_KEY_HEX>'
export RELEASE_TAG=v1.13.3
export RELEASE_BASE="https://github.com/ALENOC/electrumx-ravencoin/releases/download/$RELEASE_TAG"
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

If the exact `v1.13.3` qualification assets are absent, any `curl -f` failure is
Scenario B FAIL/STOP before trust mutation. A different `latest` release is
irrelevant because this procedure never follows `latest`.

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
assert p['electrumxVersion'] == '1.13.3'
assert p['artifact_revision'] == 0
assert p['sourceCommit'] == sys.argv[2]
print('candidate_source_identity=PASS')
PY
```

Expected output: `candidate_source_identity=PASS`.

## B2. Perform the explicit public trust-root transition

This is the one manual trust reset. It is intentionally not signed by or
approved through the retired 1.13.1 key.

Before overwriting the old public key, capture its exact bytes in the evidence
directory and prove they equal the B0 copy:

```sh
sudo cp --preserve=mode,ownership,timestamps \
  "$INSTALL_ROOT/core-safety/production/update-signing-public-key.hex" \
  "$EVIDENCE/pre-b2-update-signing-public-key.hex"
sudo cmp -s "$EVIDENCE/before-update-signing-public-key.hex" \
  "$EVIDENCE/pre-b2-update-signing-public-key.hex"
sha256sum "$EVIDENCE/pre-b2-update-signing-public-key.hex" \
  | tee "$EVIDENCE/pre-b2-update-signing-public-key.sha256"
```

Then perform the public-key transition:

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
save_state(str(p), UpdateState(failure_reason='manual 1.13.1 -> 1.13.3 trust-root transition'))
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

## Scenario B abort and restore — mandatory for any failure at B3, B4, B5 or B6

A Scenario B FAIL must end with the original 1.13.1 node serving again. The
external B0.5 checkpoint is authoritative even if the updater reports that its
own automatic rollback succeeded.

**[ELEVATED] [DESTRUCTIVE RESTORE]** The following common restore rewinds the
install root, the four bind-backed data directories, updater state and host
high-water namespace to their B0.5 contents/presence. It intentionally discards
all qualification mutations after first preserving non-secret failure evidence.

Run this exact restore for a failure at **any** of B3, B4, B5 or B6:

```sh
export INSTALL_ROOT=/opt/electrumx-ravencoin
BACKUP_ROOT="$(cat "$EVIDENCE/backup-root.txt")"
sudo test "$(cat "$BACKUP_ROOT/VERIFIED")" = 'backup=VERIFIED'
STATE_DIR="$(dirname "$INSTALL_ROOT")/.$(basename "$INSTALL_ROOT").state"
HIGH_WATER_DIR=/var/lib/electrumx-ravencoin
INSTALL_PARENT="$(dirname "$INSTALL_ROOT")"
INSTALL_NAME="$(basename "$INSTALL_ROOT")"

mapfile -t STORAGE_PATHS < <(python3 - "$EVIDENCE/before-storage.env" <<'PY'
import sys
keys=(
 'RAVENCOIN_DATA_HOST_DIR','RAVENCOIN_CONFIG_HOST_DIR',
 'ELECTRUMX_DATA_HOST_DIR','MONITOR_DATA_HOST_DIR')
values={}
for line in open(sys.argv[1], encoding='utf-8'):
    k,v=line.rstrip('\n').split('=',1); values[k]=v
assert set(values) == set(keys)
for k in keys:
    print(values[k])
PY
)
STORAGE_NAMES=(ravencoin-data ravencoin-config electrumx-data monitor-data)
test "${#STORAGE_PATHS[@]}" -eq 4
read -r RESTORE_HEIGHT RESTORE_HASH < "$EVIDENCE/restore-tip.txt"

# Preserve non-secret failure metadata before destroying the failed runtime.
docker ps -a --filter label=com.docker.compose.project=electrumx-ravencoin \
  --format '{{.ID}} {{.Image}} {{.Status}} {{.Names}}' \
  | tee "$EVIDENCE/abort-containers.txt"
TRANSACTION_JOURNAL="$INSTALL_PARENT/.$INSTALL_NAME.update-transaction.json"
if sudo test -f "$TRANSACTION_JOURNAL"; then
  sudo cp "$TRANSACTION_JOURNAL" "$EVIDENCE/abort-update-transaction.json"
fi

# Stop/remove project containers only. Do not remove bind data.
PROJECT_IDS="$(docker ps -aq --filter label=com.docker.compose.project=electrumx-ravencoin)"
if [ -n "$PROJECT_IDS" ]; then docker rm -f $PROJECT_IDS; fi

# Remove failed/current control trees and restore the exact B0.5 install tree.
sudo rm -rf "$INSTALL_ROOT"
sudo rm -rf \
  "$INSTALL_PARENT/.$INSTALL_NAME.release-staging-"* \
  "$INSTALL_PARENT/.$INSTALL_NAME.last-known-good-"* \
  "$INSTALL_PARENT/.$INSTALL_NAME.failed-update-"* \
  "$TRANSACTION_JOURNAL"
sudo rsync -aHAX --numeric-ids --delete "$BACKUP_ROOT/install-root/" "$INSTALL_ROOT/"

# Restore all four data/config DB directories to the B0.5 checkpoint.
for i in 0 1 2 3; do
  sudo install -d "${STORAGE_PATHS[$i]}"
  sudo rsync -aHAX --numeric-ids --delete \
    "$BACKUP_ROOT/data/${STORAGE_NAMES[$i]}/" "${STORAGE_PATHS[$i]}/"
done

# Restore exact pre-qualification control-state presence/content.
sudo rm -rf "$STATE_DIR"
if grep -Fxq 'STATE_DIR=present' "$EVIDENCE/prequal-control-presence.txt"; then
  sudo install -d "$STATE_DIR"
  sudo rsync -aHAX --numeric-ids --delete "$BACKUP_ROOT/updater-state/" "$STATE_DIR/"
fi
sudo rm -rf "$HIGH_WATER_DIR"
if grep -Fxq 'HIGH_WATER_DIR=present' "$EVIDENCE/prequal-control-presence.txt"; then
  sudo install -d "$HIGH_WATER_DIR"
  sudo rsync -aHAX --numeric-ids --delete "$BACKUP_ROOT/high-water/" "$HIGH_WATER_DIR/"
fi

# Verify restored bytes before restarting anything.
sudo rsync -aHAXn --numeric-ids --delete --itemize-changes \
  "$BACKUP_ROOT/install-root/" "$INSTALL_ROOT/" | tee "$EVIDENCE/restore-verify-install-root.txt"
test ! -s "$EVIDENCE/restore-verify-install-root.txt"
for i in 0 1 2 3; do
  sudo rsync -aHAXn --numeric-ids --delete --itemize-changes \
    "$BACKUP_ROOT/data/${STORAGE_NAMES[$i]}/" "${STORAGE_PATHS[$i]}/" \
    | tee "$EVIDENCE/restore-verify-${STORAGE_NAMES[$i]}.txt"
  test ! -s "$EVIDENCE/restore-verify-${STORAGE_NAMES[$i]}.txt"
done
sudo cmp -s "$EVIDENCE/pre-b2-update-signing-public-key.hex" \
  "$INSTALL_ROOT/core-safety/production/update-signing-public-key.hex"

# Recreate the four bind-volume proxy objects from restored 1.13.1 Compose.
for v in ravencoin-data ravencoin-config electrumx-data monitor-data; do
  docker volume rm "electrumx-ravencoin_${v}" >/dev/null 2>&1 || true
done
cd "$INSTALL_ROOT"
BASE_COMPOSE='docker compose -p electrumx-ravencoin -f compose.yaml -f compose.storage.yaml'
$BASE_COMPOSE up -d --no-build

# Prove the original 1.13.1 node is serving the restored chain again.
for i in $(seq 1 120); do
  CORE_HEIGHT="$($BASE_COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockcount 2>/dev/null | tr -d '\r\n[:space:]' || true)"
  RESTORED_HASH="$($BASE_COMPOSE exec -T ravencoin-core raven-cli -datadir=/var/lib/ravencoin -conf=/var/lib/ravencoin-config/raven.conf getblockhash "$RESTORE_HEIGHT" 2>/dev/null | tr -d '\r\n[:space:]' || true)"
  INFO="$($BASE_COMPOSE exec -T electrumx electrumx_rpc getinfo 2>/dev/null || true)"
  if [ "$RESTORED_HASH" = "$RESTORE_HASH" ] && [ -n "$CORE_HEIGHT" ] && [ -n "$INFO" ] && \
     python3 - "$CORE_HEIGHT" "$INFO" <<'PY'
import json, sys
h=int(sys.argv[1]); info=json.loads(sys.argv[2])
assert str(info.get('version','')).endswith('1.13.1')
assert info.get('db height') == h
assert info.get('daemon height') == h
PY
  then
    echo 'scenario_B_restore=PASS'
    break
  fi
  sleep 15
  test "$i" -lt 120
done
```

Expected final checkpoint: exactly `scenario_B_restore=PASS`. Once networking is
back, Core may advance beyond the B0.5 height; the immutable comparison is that
`getblockhash $RESTORE_HEIGHT` remains exactly `$RESTORE_HASH`, while ElectrumX
1.13.1 catches the current Core tip.

Failure-specific rule:

- **B3 FAIL:** B2 has already changed the public trust root and operational/high-
  water state. Run the full restore above. Do not reuse `$TRANSITION`, the v3
  pending/check state, the new high-water namespace, or the overwritten key.
- **B4 FAIL:** the updater may have built 1.13.3, stopped the old stack, switched
  roots, or performed its own rollback. Regardless of its verdict, run the full
  restore above. Do not reuse any release-staging, last-known-good, failed-update
  directory, transaction journal, v3 state, or candidate runtime as a retry base.
- **B5 FAIL:** 1.13.3 may already be promoted and high-water may have advanced.
  Run the full restore above, including all four bind directories and control
  state. Do not reuse the promoted install root or high-water state.
- **B6 FAIL:** treat the running 1.13.3 node as unqualified. Run the full restore
  above, including all four bind directories and control state. Do not reuse its
  ElectrumX DB/runtime state for another qualification attempt.

The candidate download files and evidence logs may be retained as evidence, but
must not be used as the starting state for a retry. Any retry starts again from
a newly verified B0/B0.5 baseline after maintainer review.

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

PASS requires exit `0` and a pending 1.13.3 candidate that verified under the
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
assert p['manifest']['electrumxVersion'] == '1.13.3'
assert p['manifest']['artifact_revision'] == 0
assert p['_verificationVerdict'] == 'VERIFIED'
print('pending_candidate=VERIFIED')
PY
```

Expected output: `pending_candidate=VERIFIED`. Any failure at B3 invokes the
mandatory Scenario B abort-and-restore section above.

## B4. Apply through the transactional updater

```sh
sudo -H env \
  ELECTRUMX_INSTALL_ROOT="$INSTALL_ROOT" \
  PYTHONPATH="$TRANSITION/core-safety/scripts" \
  python3 "$TRANSITION/core-safety/scripts/electrumx_update_cli.py" apply \
  | tee "$EVIDENCE/update-apply.txt"
```

A non-zero updater exit is B4 FAIL: do not continue; run Scenario B abort and
restore. If it exits `0`, the evidence must contain all three exact lines below:

```sh
grep -Fx 'UPDATER_CHECKPOINT storage-preflight=PASS old-stack=RUNNING bind-paths=4 volume-objects=4 active-mounts=PASS' \
  "$EVIDENCE/update-apply.txt"
grep -Fx 'UPDATER_CHECKPOINT candidate-storage=PASS old-stack=RUNNING compose-model=PASS bind-paths=4' \
  "$EVIDENCE/update-apply.txt"
grep -Fx 'UPDATER_CHECKPOINT release-switch=PASS same-filesystem-renames=COMPLETE new-root=ACTIVE' \
  "$EVIDENCE/update-apply.txt"
```

These are load-bearing qualification checkpoints:

- `storage-preflight=PASS` is emitted only after the running Compose storage
  model, four existing Docker bind-volume objects and active old-container mounts
  have all been proved while the old stack is still running;
- `candidate-storage=PASS` is emitted only after the staged candidate `.env` and
  Compose model have been proved to preserve those exact four host paths, still
  before the old stack is stopped;
- `release-switch=PASS` is emitted only after the same-filesystem old-root-to-
  backup and staged-root-to-install-root renames have both completed and the new
  root is active.

PASS also requires promotion to current. The updater itself must:

- prove all four existing host bind paths before stopping the old node;
- prove Docker volume objects point to those paths;
- prove active old containers are mounted on those exact volume objects;
- stage/build 1.13.3 while the old node is still running;
- copy only the allowlisted operator state;
- prove the candidate Compose model resolves to the same four host paths;
- stop the old stack;
- perform the transactional same-filesystem release-directory switch;
- re-prove storage before starting the new stack;
- start 1.13.3 without running the ChainStrap one-shot again;
- pass Core/ElectrumX health gates;
- advance the host-global high-water only after promotion.

Any updater rollback or STUCK verdict is Scenario B **FAIL**, even if the old node
is successfully restored by the updater. Run the external abort-and-restore
procedure before returning the evidence.

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

Expected output: `docker_volume_bindings=UNCHANGED`. Any B5 failure invokes the
mandatory Scenario B abort-and-restore procedure.

## B6. Prove chain continuity and 1.13.3 service identity

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
assert str(info.get('version','')).endswith('1.13.3')
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

PASS: version ends in 1.13.3 and ElectrumX DB/daemon heights equal Core. Any B6
failure invokes the mandatory Scenario B abort-and-restore procedure.

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
assert s['highestAcceptedVersion'] == '1.13.3'
r=s['releases']['1.13.3']
assert r['artifact_revision'] == 0
print(json.dumps(s, indent=2, sort_keys=True))
PY
```

PASS requires the installed public key to equal the out-of-band key, locator
`root:root 644`, state `root:root 600`, highest version 1.13.3 and revision 0.

### Scenario B PASS

Scenario B is PASS only when B0 through B8 all pass. Record:

```sh
printf 'scenario=B\nresult=PASS\nfinished=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  | tee "$EVIDENCE/RESULT.txt"
```

---

# Evidence return contract

For each scenario return one compressed evidence directory containing the files
created above plus the complete `executor.log`. Do **not** include secrets or the
B0.5 rollback copy.

The executor's final report must contain only:

```text
Scenario A: PASS|FAIL
Scenario B: PASS|FAIL
A evidence: <path/archive>
B evidence: <path/archive>
Qualified release: 1.13.3 artifact_revision=0 tag=v1.13.3
Qualified source commit: <release-provenance sourceCommit>
Resolved ChainStrap snapshot in A: <sourceCommit> <metadata_sha256> <height> <blockhash>
Failure checkpoint: <none or exact A#/B# checkpoint>
Scenario B restore after FAIL: PASS|not-required
```

A PASS is not inferred from a generally healthy node. Every checkpoint above is
part of the release evidence. If either scenario is FAIL, PR3 remains unmerged.
