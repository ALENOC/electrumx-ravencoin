#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one anchor, found {count}")
    p.write_text(text.replace(old, new, 1))


def append_once(path, marker, addition):
    p = Path(path)
    text = p.read_text()
    if addition.strip() in text:
        raise SystemExit(f"{path}: addition already present")
    if marker not in text:
        raise SystemExit(f"{path}: marker missing")
    p.write_text(text.replace(marker, marker + addition, 1))


# 1. LogicalFile durable writes: file fsync + parent-directory fsync for new segments.
replace_once(
    "electrumx/lib/util.py",
    "import logging\nimport sys\n",
    "import logging\nimport os\nimport sys\n",
)
replace_once(
    "electrumx/lib/util.py",
'''    def write(self, start, b):
        \'\'\'Write the bytes-like object, b, to the underlying virtual file.\'\'\'
        while b:
            size = min(len(b), self.file_size - (start % self.file_size))
            with self.open_file(start, True) as f:
                f.write(b if size == len(b) else b[:size])
            b = b[size:]
            start += size
''',
'''    def write(self, start, b, *, sync=False):
        \'\'\'Write b to the virtual file.

        If sync is true, every touched segment is fsync'd before this method
        returns.  Newly-created segment directory entries are fsync'd too.
        This is the durability barrier used by DB.flush_fs() before the
        corresponding LevelDB state batch is allowed to commit.
        \'\'\'
        created_dirs = set()
        while b:
            size = min(len(b), self.file_size - (start % self.file_size))
            file_num, _offset = divmod(start, self.file_size)
            filename = self.filename_fmt.format(file_num)
            existed = os.path.exists(filename)
            with self.open_file(start, True) as f:
                f.write(b if size == len(b) else b[:size])
                if sync:
                    f.flush()
                    os.fsync(f.fileno())
            if sync and not existed:
                created_dirs.add(os.path.dirname(filename) or '.')
            b = b[size:]
            start += size

        # fsyncing a newly-created file does not by itself guarantee that its
        # directory entry survives sudden power loss.  Persist those entries
        # before the LevelDB commit can advance chain state.
        if sync:
            flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
            for directory in sorted(created_dirs):
                fd = os.open(directory, flags)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
'''
)

# 2. DB startup inspection and durable metadata flush.
replace_once(
    "electrumx/server/db.py",
    "        self.tx_counts = None\n        \n        self.asset_db: Storage = None\n",
    "        self.tx_counts = None\n        self.fs_metadata_issue = None\n        \n        self.asset_db: Storage = None\n",
)
replace_once(
    "electrumx/server/db.py",
'''    async def _read_tx_counts(self):
        if self.tx_counts is not None:
            return
        # tx_counts[N] has the cumulative number of txs at the end of
        # height N.  So tx_counts[0] is 1 - the genesis coinbase
        size = (self.state.height + 1) * 8
        tx_counts = self.tx_counts_file.read(0, size)
        assert len(tx_counts) == size
        self.tx_counts = array('Q', tx_counts)
        if self.tx_counts:
            assert self.state.tx_count == self.tx_counts[-1]
        else:
            assert self.state.tx_count == 0
''',
'''    async def _read_tx_counts(self, *, force=False, strict=False):
        if self.tx_counts is not None and not force:
            return
        # tx_counts[N] has the cumulative number of txs at the end of
        # height N.  So tx_counts[0] is 1 - the genesis coinbase.
        size = (self.state.height + 1) * 8
        raw = self.tx_counts_file.read(0, size)
        issue = None
        if len(raw) != size:
            issue = (f'txcounts metadata is truncated: got {len(raw):,d} bytes, '
                     f'expected {size:,d} for height {self.state.height:,d}')
        usable = raw[:len(raw) // 8 * 8]
        counts = array('Q', usable)

        previous = 0
        if issue is None:
            for height, count in enumerate(counts):
                # Every Ravencoin block contains at least its coinbase.
                if count <= previous:
                    issue = (f'txcounts metadata is non-monotonic at height {height:,d}: '
                             f'{count:,d} after {previous:,d}')
                    break
                previous = count
        if issue is None:
            if counts:
                if self.state.tx_count != counts[-1]:
                    issue = (f'UTXO state tx_count {self.state.tx_count:,d} does not match '
                             f'txcounts tail {counts[-1]:,d} at height '
                             f'{self.state.height:,d}')
            elif self.state.tx_count != 0:
                issue = (f'UTXO state tx_count is {self.state.tx_count:,d} but txcounts '
                         'metadata is empty')

        self.tx_counts = counts
        self.fs_metadata_issue = issue
        if issue:
            self.logger.critical(
                f'filesystem metadata crash-consistency check failed: {issue}; '
                'bounded trailing-metadata recovery will run before indexing')
            if strict:
                raise self.DBError(issue)

    def fs_metadata_needs_recovery(self):
        if self.fs_metadata_issue:
            return True
        if self.state.height < 0:
            return False
        if len(self.tx_counts) != self.state.height + 1:
            self.fs_metadata_issue = 'txcounts metadata does not cover the committed DB height'
            return True

        height = self.state.height
        header_len = self.header_len(height)
        header = self.headers_file.read(self.header_offset(height), header_len)
        if len(header) != header_len:
            self.fs_metadata_issue = f'header metadata is truncated at height {height:,d}'
            return True
        try:
            if self.coin.header_hash(header) != self.state.tip:
                self.fs_metadata_issue = f'header metadata tip mismatch at height {height:,d}'
                return True
        except Exception as exc:
            self.fs_metadata_issue = f'header metadata is unreadable at height {height:,d}: {exc}'
            return True

        first_tx = self.tx_counts[height - 1] if height > 0 else 0
        block_tx_count = self.state.tx_count - first_tx
        if block_tx_count <= 0:
            self.fs_metadata_issue = f'invalid transaction count for committed height {height:,d}'
            return True
        hashes = self.hashes_file.read(first_tx * 32, block_tx_count * 32)
        if len(hashes) != block_tx_count * 32:
            self.fs_metadata_issue = f'transaction-hash metadata is truncated at height {height:,d}'
            return True
        if any(hashes[offset:offset + 32] == bytes(32)
               for offset in range(0, len(hashes), 32)):
            self.fs_metadata_issue = f'zero transaction-hash slot detected at height {height:,d}'
            return True
        return False
'''
)
replace_once(
    "electrumx/server/db.py",
'''        self.headers_file.write(offset, b''.join(flush_data.headers))
        flush_data.headers.clear()

        offset = height_start * self.tx_counts.itemsize
        self.tx_counts_file.write(offset,
                                  self.tx_counts[height_start:].tobytes())
        offset = prior_tx_count * 32
        self.hashes_file.write(offset, hashes)
''',
'''        # Crash-consistency barrier: flat-file metadata must be durable
        # before History/LevelDB commits can advance the authoritative state.
        # Without this ordering a hard power loss can leave LevelDB at H while
        # headers/hashes/txcounts are still only in the page cache for H.
        self.headers_file.write(offset, b''.join(flush_data.headers), sync=True)
        flush_data.headers.clear()

        offset = height_start * self.tx_counts.itemsize
        self.tx_counts_file.write(offset,
                                  self.tx_counts[height_start:].tobytes(), sync=True)
        offset = prior_tx_count * 32
        self.hashes_file.write(offset, hashes, sync=True)
'''
)

# 3. Bounded startup repair using raw blocks from the already-trusted daemon.
replace_once(
    "electrumx/server/block_processor.py",
    "class BlockProcessor:\n",
    "FS_METADATA_RECOVERY_MAX_BLOCKS = 64\n\n\nclass BlockProcessor:\n",
)
replace_once(
    "electrumx/server/block_processor.py",
'''    # --- External API

    async def fetch_and_process_blocks(self, caught_up_event, shutdown_event):
''',
'''    async def _repair_trailing_fs_metadata(self):
        \'\'\'Repair only a bounded, daemon-verified trailing metadata window.

        This is intentionally conservative.  We trust the LevelDB state only
        after its tip is proven to match the daemon, and we anchor the repair
        to the cumulative tx count immediately before the recovery window.  If
        either check fails, automatic repair is refused.
        \'\'\'
        if not self.db.fs_metadata_needs_recovery():
            return False

        state = self.db.state
        if state.height < 0:
            raise self.db.DBError('filesystem metadata recovery requested for an empty DB')

        count = min(FS_METADATA_RECOVERY_MAX_BLOCKS, state.height + 1)
        start = state.height - count + 1
        hex_hashes = await self.daemon.block_hex_hashes(start, count)
        if len(hex_hashes) != count:
            raise self.db.DBError('daemon did not return the complete metadata recovery window')
        if hex_hashes[-1] != hash_to_hex_str(state.tip):
            raise self.db.DBError(
                'refusing filesystem metadata repair: committed LevelDB tip does not match daemon tip')

        os.makedirs(OnDiskBlock.path, exist_ok=True)
        await OnDiskBlock.prefetch_many(
            self.daemon, enumerate(hex_hashes, start=start), 'metadata-recovery')

        expected_headers = []
        expected_hashes = []
        txs_per_block = []
        if start > 0:
            prior_len = self.coin.static_header_len(start - 1)
            prior = self.db.headers_file.read(self.db.header_offset(start - 1), prior_len)
            if len(prior) != prior_len:
                raise self.db.DBError(
                    'refusing filesystem metadata repair: recovery boundary header is missing')
            expected_prev = self.coin.header_hash(prior)
        else:
            expected_prev = None

        try:
            for height, hex_hash in enumerate(hex_hashes, start=start):
                block = await OnDiskBlock.streamed_block(self.coin, hex_hash)
                if block is None:
                    raise self.db.DBError(
                        f'daemon raw block unavailable for metadata recovery at height {height:,d}')
                with block:
                    self.coin.validate_header(block.header, height)
                    if expected_prev is not None and self.coin.header_prevhash(block.header) != expected_prev:
                        raise self.db.DBError(
                            f'refusing filesystem metadata repair: chain boundary mismatch at '
                            f'height {height:,d}')
                    expected_prev = self.coin.header_hash(block.header)
                    block_hashes = [tx_hash for _tx, tx_hash in block.iter_txs()]
                    if not block_hashes:
                        raise self.db.DBError(
                            f'daemon raw block has no transactions at height {height:,d}')
                    expected_headers.append(block.header)
                    expected_hashes.extend(block_hashes)
                    txs_per_block.append(len(block_hashes))
        finally:
            # Recovery downloads are scratch data, never part of the durable DB.
            await OnDiskBlock.delete_stale(hex_hashes, False)

        if expected_prev != state.tip:
            raise self.db.DBError(
                'refusing filesystem metadata repair: reconstructed tail does not end at LevelDB tip')

        tail_tx_count = sum(txs_per_block)
        base_tx_count = state.tx_count - tail_tx_count
        if base_tx_count < 0:
            raise self.db.DBError('refusing filesystem metadata repair: invalid committed tx_count')
        if start > 0:
            raw_prior_count = self.db.tx_counts_file.read((start - 1) * 8, 8)
            if len(raw_prior_count) != 8:
                raise self.db.DBError(
                    'refusing filesystem metadata repair: recovery boundary txcount is missing')
            prior_count = array('Q', raw_prior_count)[0]
            if prior_count != base_tx_count:
                raise self.db.DBError(
                    'refusing automatic metadata repair: corruption extends beyond the bounded '
                    f'{count}-block recovery window')
        elif base_tx_count != 0:
            raise self.db.DBError('refusing filesystem metadata repair: genesis boundary mismatch')

        cumulative = []
        running = base_tx_count
        for block_tx_count in txs_per_block:
            running += block_tx_count
            cumulative.append(running)
        if running != state.tx_count:
            raise self.db.DBError('refusing filesystem metadata repair: reconstructed tx_count mismatch')

        headers_blob = b''.join(expected_headers)
        counts_blob = array('Q', cumulative).tobytes()
        hashes_blob = b''.join(expected_hashes)

        self.db.logger.warning(
            f'repairing trailing filesystem metadata at heights {start:,d}-{state.height:,d} '
            f'after crash-consistency failure: {self.db.fs_metadata_issue}')
        self.db.headers_file.write(self.db.header_offset(start), headers_blob, sync=True)
        self.db.tx_counts_file.write(start * 8, counts_blob, sync=True)
        self.db.hashes_file.write(base_tx_count * 32, hashes_blob, sync=True)

        await self.db._read_tx_counts(force=True, strict=True)
        self.db.fs_height = state.height
        self.db.fs_tx_count = state.tx_count
        self.db.fs_asset_count = state.asset_count
        self.db.fs_h160_count = state.h160_count
        self.db.logger.warning(
            f'filesystem metadata self-heal completed through height {state.height:,d}; '
            'normal chain verification will run before indexing resumes')
        return True

    # --- External API

    async def fetch_and_process_blocks(self, caught_up_event, shutdown_event):
'''
)
replace_once(
    "electrumx/server/block_processor.py",
'''        self.state = OnDiskBlock.state = (await self.db.open_for_sync()).copy()
        # Refuse a stale or forked index before extending or serving it.  This
        # requires the open database above, so it cannot run any earlier.
        await verify_database_chain(self.db, self.daemon)
''',
'''        await self.db.open_for_sync()
        # Detect/recover the narrow trailing flat-file failure mode before any
        # query or block-processing path is allowed to consume tx_counts/hash
        # metadata.  Repair is bounded and anchored to the daemon + LevelDB tip.
        await self._repair_trailing_fs_metadata()
        self.state = OnDiskBlock.state = self.db.state.copy()
        # Refuse a stale or forked index before extending or serving it.  This
        # requires the open database above, so it cannot run any earlier.
        await verify_database_chain(self.db, self.daemon)
'''
)

# 4. Monitor failure-domain isolation. Keep local admin RPC and add a separate
# internal-only RPC endpoint used only by the monitor network.
replace_once(
    "compose.yaml",
    '      SERVICES: tcp://:50001,rpc://127.0.0.1:8000\n',
    '      SERVICES: tcp://:50001,rpc://127.0.0.1:8000,rpc://172.29.81.2:8001\n',
)
replace_once(
    "compose.yaml",
'''    networks:
      ravencoin-backend:
        ipv4_address: 172.29.80.20
''',
'''    networks:
      ravencoin-backend:
        ipv4_address: 172.29.80.20
      monitor-admin:
        ipv4_address: 172.29.81.2
'''
)
replace_once(
    "compose.yaml",
'''networks:
  ravencoin-backend:
    driver: bridge
    ipam:
      config:
        - subnet: 172.29.80.0/24
''',
'''networks:
  ravencoin-backend:
    driver: bridge
    ipam:
      config:
        - subnet: 172.29.80.0/24
  monitor-admin:
    driver: bridge
    internal: true
    ipam:
      config:
        - subnet: 172.29.81.0/29
'''
)
replace_once(
    "compose.tls.yaml",
    '      SERVICES: ssl://:50002,rpc://127.0.0.1:8000\n',
    '      SERVICES: ssl://:50002,rpc://127.0.0.1:8000,rpc://172.29.81.2:8001\n',
)
Path("compose.monitor.yaml").write_text('''services:\n  monitor:\n    build:\n      context: ./vendor/ravencoin-node-monitor\n    image: electrumx-ravencoin-node-monitor:bundled\n    container_name: ravencoin-node-monitor\n    restart: unless-stopped\n    depends_on:\n      rpc-secrets-init:\n        condition: service_completed_successfully\n    # Failure-domain isolation: the monitor must remain alive when ElectrumX\n    # is unhealthy or crash-looping.  Core is reached on ravencoin-backend;\n    # ElectrumX admin RPC is reachable only over the dedicated internal\n    # monitor-admin network and is never published to the host.\n    networks:\n      ravencoin-backend: {}\n      monitor-admin:\n        ipv4_address: 172.29.81.3\n    env_file:\n      - ./vendor/ravencoin-node-monitor/.env\n    environment:\n      CORE_RPC_HOST: ravencoin-core\n      CORE_RPC_PORT: "8766"\n      CORE_RPC_USER_FILE: /run/raven-secrets/raven_rpc_user\n      CORE_RPC_PASSWORD_FILE: /run/raven-secrets/raven_rpc_password\n      ELECTRUMX_ENABLED: "true"\n      ELECTRUMX_RPC_HOST: 172.29.81.2\n      ELECTRUMX_RPC_PORT: "8001"\n      ELECTRUMX_SSL_HOST: electrumx\n      ELECTRUMX_SSL_PORT: "50002"\n    volumes:\n      - rpc-secrets:/run/raven-secrets:ro\n      - monitor-data:/data\n    read_only: true\n    security_opt:\n      - no-new-privileges:true\n    cap_drop:\n      - ALL\n    tmpfs:\n      - /tmp:size=32m,mode=1777\n    ports:\n      - "127.0.0.1:8899:8899/tcp"\n\nvolumes:\n  monitor-data:\n''')

# 5. Regression tests.
append_once(
    "tests/lib/test_util.py",
    "    assert L.read(0, -1) == b'957' * 6\n",
'''\n\ndef test_LogicalFile_durable_write_fsyncs_data_and_new_segment_directory(tmpdir, monkeypatch):
    prefix = os.path.join(tmpdir, 'durable')
    logical = util.LogicalFile(prefix, 2, 6)
    fsync_calls = []
    monkeypatch.setattr(util.os, 'fsync', lambda fd: fsync_calls.append(fd))

    logical.write(0, b'0123456789', sync=True)

    # Two segment files plus at least one directory durability barrier.
    assert len(fsync_calls) >= 3
    assert logical.read(0, -1) == b'0123456789'
'''
)

Path("tests/server/test_db_crash_consistency.py").write_text('''import logging\nfrom array import array\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom electrumx.lib.hash import hash_to_hex_str\nfrom electrumx.server import block_processor as bp_module\nfrom electrumx.server.db import DB\n\n\nclass RecordingFile:\n    def __init__(self, reads=None):\n        self.reads = reads or {}\n        self.writes = []\n\n    def read(self, start, size=-1):\n        return self.reads.get((start, size), b"")\n\n    def write(self, start, data, *, sync=False):\n        self.writes.append((start, data, sync))\n\n\n@pytest.mark.asyncio\nasync def test_txcounts_torn_tail_is_detected_without_assert_crash():\n    db = DB.__new__(DB)\n    db.state = SimpleNamespace(height=1, tx_count=2)\n    db.tx_counts = None\n    db.fs_metadata_issue = None\n    db.tx_counts_file = RecordingFile({(0, 16): array("Q", [1, 0]).tobytes()})\n    db.logger = logging.getLogger("test")\n\n    await DB._read_tx_counts(db)\n    assert db.fs_metadata_issue is not None\n    assert "non-monotonic" in db.fs_metadata_issue\n\n    with pytest.raises(DB.DBError):\n        await DB._read_tx_counts(db, force=True, strict=True)\n\n\ndef test_flush_fs_uses_durable_writes_for_all_three_flat_metadata_files():\n    db = DB.__new__(DB)\n    db.fs_height = -1\n    db.fs_tx_count = 0\n    db.fs_asset_count = 0\n    db.fs_h160_count = 0\n    db.tx_counts = array("Q", [1])\n    db.header_offset = lambda height: height * 80\n    db.headers_file = RecordingFile()\n    db.tx_counts_file = RecordingFile()\n    db.hashes_file = RecordingFile()\n\n    state = SimpleNamespace(height=0, tx_count=1, asset_count=0, h160_count=0)\n    flush = SimpleNamespace(\n        state=state, headers=[b"h" * 80], block_tx_hashes=[b"x" * 32])\n\n    DB.flush_fs(db, flush)\n\n    assert db.headers_file.writes[0][2] is True\n    assert db.tx_counts_file.writes[0][2] is True\n    assert db.hashes_file.writes[0][2] is True\n\n\n@pytest.mark.asyncio\nasync def test_bounded_startup_repair_rebuilds_torn_tail_from_daemon(monkeypatch):\n    tip = b"t" * 32\n    tx_hash = b"x" * 32\n    header = b"h" * 80\n\n    class Coin:\n        @staticmethod\n        def static_header_len(height):\n            return 80\n\n        @staticmethod\n        def validate_header(raw, height):\n            assert raw == header\n            assert height == 0\n\n        @staticmethod\n        def header_prevhash(raw):\n            return bytes(32)\n\n        @staticmethod\n        def header_hash(raw):\n            return tip\n\n    class FakeBlock:\n        height = 0\n        size = 100\n        def __enter__(self):\n            self.header = header\n            return self\n        def __exit__(self, *args):\n            return False\n        def iter_txs(self):\n            yield object(), tx_hash\n\n    class Daemon:\n        async def block_hex_hashes(self, start, count):\n            assert (start, count) == (0, 1)\n            return [hash_to_hex_str(tip)]\n\n    async def prefetch_many(*args, **kwargs):\n        return None\n    async def streamed_block(*args, **kwargs):\n        return FakeBlock()\n    async def delete_stale(*args, **kwargs):\n        return None\n\n    monkeypatch.setattr(bp_module.OnDiskBlock, "prefetch_many", prefetch_many)\n    monkeypatch.setattr(bp_module.OnDiskBlock, "streamed_block", streamed_block)\n    monkeypatch.setattr(bp_module.OnDiskBlock, "delete_stale", delete_stale)\n\n    db = SimpleNamespace(\n        state=SimpleNamespace(height=0, tx_count=1, asset_count=0, h160_count=0, tip=tip),\n        fs_metadata_issue="torn txcounts/hash tail",\n        logger=logging.getLogger("test"),\n        header_offset=lambda height: height * 80,\n        headers_file=RecordingFile(),\n        tx_counts_file=RecordingFile(),\n        hashes_file=RecordingFile(),\n        fs_height=-1, fs_tx_count=0, fs_asset_count=0, fs_h160_count=0,\n        DBError=DB.DBError,\n    )\n    db.fs_metadata_needs_recovery = lambda: True\n    async def reread(*, force=False, strict=False):\n        assert force and strict\n        db.fs_metadata_issue = None\n    db._read_tx_counts = reread\n\n    processor = bp_module.BlockProcessor.__new__(bp_module.BlockProcessor)\n    processor.db = db\n    processor.daemon = Daemon()\n    processor.coin = Coin()\n\n    assert await processor._repair_trailing_fs_metadata() is True\n    assert db.headers_file.writes == [(0, header, True)]\n    assert db.tx_counts_file.writes == [(0, array("Q", [1]).tobytes(), True)]\n    assert db.hashes_file.writes == [(0, tx_hash, True)]\n    assert db.fs_height == 0\n    assert db.fs_tx_count == 1\n''')

Path("tests/test_monitor_failure_isolation.py").write_text('''from pathlib import Path\n\n\ndef test_monitor_does_not_share_electrumx_network_namespace_or_health_dependency():\n    monitor = Path("compose.monitor.yaml").read_text()\n    assert "network_mode:" not in monitor\n    assert "condition: service_healthy" not in monitor\n    assert "rpc-secrets-init:" in monitor\n    assert "condition: service_completed_successfully" in monitor\n    assert "ELECTRUMX_RPC_HOST: 172.29.81.2" in monitor\n    assert 'ELECTRUMX_RPC_PORT: "8001"' in monitor\n    assert '"127.0.0.1:8899:8899/tcp"' in monitor\n\n\ndef test_monitor_admin_rpc_is_internal_only_and_survives_public_service_mode_changes():\n    base = Path("compose.yaml").read_text()\n    tls = Path("compose.tls.yaml").read_text()\n    assert "rpc://172.29.81.2:8001" in base\n    assert "rpc://172.29.81.2:8001" in tls\n    assert "monitor-admin:" in base\n    assert "internal: true" in base\n    assert "8001:8001" not in base\n    assert "8001:8001" not in tls\n''')

Path("docs/crash-consistency.md").write_text('''# ElectrumX crash consistency and monitor failure isolation\n\nAfter an unclean host shutdown, a live Ravencoin ElectrumX deployment was\nobserved with LevelDB state committed through a block while the corresponding\n`meta/hashes` and `meta/txcounts` tail was still zero/unwritten.  The old\nstartup path asserted on that mismatch and crash-looped.\n\nThe hardened design has three layers:\n\n1. `DB.flush_fs()` writes headers, cumulative tx counts and transaction hashes\n   with an fsync barrier (including new segment directory entries) **before**\n   History/LevelDB state can commit.  A power loss can therefore leave flat\n   files ahead of LevelDB, which is safe because the committed DB height is the\n   authoritative prefix; it must not leave LevelDB ahead of non-durable flat\n   metadata.\n2. Startup no longer treats a trailing tx-count mismatch as a Python assertion.\n   It detects truncated/non-monotonic/mismatched metadata plus a missing/zero\n   committed-tip hash slot.  Before indexing, a bounded recovery of at most 64\n   trailing blocks may rebuild headers/txcounts/hashes from the trusted daemon.\n   Recovery is allowed only when the daemon tip matches the committed LevelDB\n   tip and the cumulative tx count immediately before the recovery window is a\n   valid anchor.  Otherwise startup fails closed and does not guess.\n3. Ravencoin Node Monitor is no longer tied to the ElectrumX container network\n   namespace or `service_healthy` dependency.  It stays alive during an\n   ElectrumX crash-loop, continues monitoring Core/host state, and reports\n   ElectrumX as unavailable.  Its admin RPC travels over a dedicated internal\n   Docker network and is not published to the host.\n\nThis is defense in depth; clean shutdown and stable power are still strongly\nrecommended, but correctness no longer depends on the kernel flushing unrelated\nflat-file page-cache writes before LevelDB's WAL/state commit.\n''')

print("crash-consistency + monitor-isolation patch applied")
