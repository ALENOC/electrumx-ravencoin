# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""SQLite persistence for the Electrum monitor.

Deliberately small: a few hundred endpoints, one file, no server to run.  The
schema is versioned and migrations only ever add, so an existing database is
never rewritten in place by an upgrade.

Retention is bounded because a crawler left running for a year will otherwise
accumulate observations forever.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Iterable, List, Optional

from .model import (
    Availability, DiscoverySource, EndpointId, EndpointState, ProbeResult, Security,
    Transport,
)

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS endpoints (
    id INTEGER PRIMARY KEY,
    hostname TEXT NOT NULL,
    port INTEGER NOT NULL,
    transport TEXT NOT NULL,
    availability TEXT NOT NULL,
    security TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    operator TEXT,
    operator_group TEXT,
    sources TEXT NOT NULL DEFAULT '[]',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    consecutive_successes INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER,
    last_seen INTEGER,
    last_probe INTEGER,
    last_success INTEGER,
    UNIQUE (hostname, port, transport)
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    endpoint_id INTEGER NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
    observed_at INTEGER NOT NULL,
    reachable INTEGER NOT NULL,
    error_category TEXT,
    server_version TEXT,
    height INTEGER,
    tip_hash TEXT,
    rpc_latency_ms REAL,
    backend_json TEXT
    ,vantage_point TEXT NOT NULL DEFAULT 'local'
);
CREATE INDEX IF NOT EXISTS observations_endpoint_time
    ON observations (endpoint_id, observed_at);

CREATE TABLE IF NOT EXISTS addresses (
    endpoint_id INTEGER NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
    family TEXT NOT NULL,
    address TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (endpoint_id, family, address)
);

CREATE TABLE IF NOT EXISTS certificates (
    endpoint_id INTEGER NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    issuer TEXT,
    not_after TEXT,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (endpoint_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS peer_edges (
    source_id INTEGER NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    announcements INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source_id, target_id)
);

CREATE TABLE IF NOT EXISTS classification_history (
    id INTEGER PRIMARY KEY,
    endpoint_id INTEGER NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
    changed_at INTEGER NOT NULL,
    availability TEXT NOT NULL,
    security TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT ''
);

-- Anti-rollback high-water mark for the signed safe-Core policy this monitor
-- has verified and accepted. A single row (id=1); the mark only ever moves
-- forward (see Store.record_policy_version).
CREATE TABLE IF NOT EXISTS policy_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    minimum_policy_version INTEGER NOT NULL DEFAULT 0
);

-- Cross-crawl confirmation counters for suspected chain conflicts (R-03).
-- A single crawl only ever produces CONFLICT_SUSPECTED; CHAIN_CONFLICT
-- requires the SAME operator group to conflict again on a later,
-- independent crawl.  Keyed on the group only (not on the exact
-- disagreeing hash), so an attacker cannot dodge confirmation by varying
-- what they lie about crawl to crawl; a positively verified clean
-- comparison (never merely the absence of a fresh conflict) clears the
-- row.  Bounded by the number of known/observed operator groups, pruned
-- alongside old observations by Store.prune().
CREATE TABLE IF NOT EXISTS chain_conflicts (
    group_key TEXT PRIMARY KEY,
    confirmations INTEGER NOT NULL DEFAULT 1,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL
);
"""


class Store:
    """Thin persistence layer.  No business logic lives here."""

    def __init__(self, path: str = "monitor.sqlite3"):
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        with self.connection:
            self.connection.executescript(SCHEMA)
            row = self.connection.execute(
                "SELECT version FROM schema_version").fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            elif row["version"] > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {row['version']} is newer than this code "
                    f"understands ({SCHEMA_VERSION}); refusing to touch it")
            else:
                if row["version"] < 2:
                    self.connection.execute(
                        "ALTER TABLE observations ADD COLUMN vantage_point TEXT "
                        "NOT NULL DEFAULT 'local'")
                # policy_state (version 3) and chain_conflicts (version 4)
                # are created unconditionally above via CREATE TABLE IF NOT
                # EXISTS; no ALTER is needed for a new table.
                if row["version"] < SCHEMA_VERSION:
                    self.connection.execute(
                        "UPDATE schema_version SET version=?", (SCHEMA_VERSION,))

    # ---------------------------------------------------------------- endpoints
    def upsert_endpoint(self, endpoint: EndpointId, *,
                        source: DiscoverySource = DiscoverySource.MANUAL,
                        operator: Optional[str] = None,
                        operator_group: Optional[str] = None,
                        now: Optional[int] = None) -> int:
        now = int(now if now is not None else time.time())
        with self.connection:
            row = self.connection.execute(
                "SELECT id, sources, operator, operator_group FROM endpoints "
                "WHERE hostname=? AND port=? AND transport=?",
                (endpoint.hostname, endpoint.port, endpoint.transport.value)).fetchone()
            if row is None:
                cursor = self.connection.execute(
                    "INSERT INTO endpoints (hostname, port, transport, availability, "
                    "security, sources, first_seen, last_seen, operator, operator_group)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (endpoint.hostname, endpoint.port, endpoint.transport.value,
                     Availability.DISCOVERED.value, Security.UNKNOWN.value,
                     json.dumps([source.value]), now, now, operator, operator_group))
                return int(cursor.lastrowid)
            sources = set(json.loads(row["sources"]))
            sources.add(source.value)
            self.connection.execute(
                "UPDATE endpoints SET sources=?, last_seen=?, operator=COALESCE(?, "
                "operator), operator_group=COALESCE(?, operator_group) WHERE id=?",
                (json.dumps(sorted(sources)), now, operator, operator_group, row["id"]))
            return int(row["id"])

    def endpoint_id(self, endpoint: EndpointId) -> Optional[int]:
        row = self.connection.execute(
            "SELECT id FROM endpoints WHERE hostname=? AND port=? AND transport=?",
            (endpoint.hostname, endpoint.port, endpoint.transport.value)).fetchone()
        return int(row["id"]) if row else None

    def load_state(self, endpoint: EndpointId) -> Optional[EndpointState]:
        row = self.connection.execute(
            "SELECT * FROM endpoints WHERE hostname=? AND port=? AND transport=?",
            (endpoint.hostname, endpoint.port, endpoint.transport.value)).fetchone()
        return self._row_to_state(row) if row else None

    def all_states(self) -> List[EndpointState]:
        rows = self.connection.execute(
            "SELECT * FROM endpoints ORDER BY hostname, port").fetchall()
        return [self._row_to_state(row) for row in rows]

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> EndpointState:
        endpoint = EndpointId(row["hostname"], int(row["port"]),
                              Transport(row["transport"]))
        return EndpointState(
            endpoint=endpoint,
            availability=Availability(row["availability"]),
            security=Security(row["security"]),
            reason=row["reason"] or "",
            operator=row["operator"],
            operator_group=row["operator_group"],
            sources={DiscoverySource(value) for value in json.loads(row["sources"])},
            consecutive_failures=int(row["consecutive_failures"]),
            consecutive_successes=int(row["consecutive_successes"]),
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            last_probe=row["last_probe"],
            last_success=row["last_success"],
        )

    def save_state(self, state: EndpointState, *, now: Optional[int] = None) -> None:
        now = int(now if now is not None else time.time())
        endpoint_id = self.endpoint_id(state.endpoint)
        if endpoint_id is None:
            endpoint_id = self.upsert_endpoint(state.endpoint, now=now)
        previous = self.connection.execute(
            "SELECT availability, security, reason FROM endpoints WHERE id=?",
            (endpoint_id,)).fetchone()
        with self.connection:
            self.connection.execute(
                "UPDATE endpoints SET availability=?, security=?, reason=?, "
                "operator=?, operator_group=?, consecutive_failures=?, "
                "consecutive_successes=?, last_seen=?, last_probe=?, last_success=? "
                "WHERE id=?",
                (state.availability.value, state.security.value, state.reason,
                 state.operator, state.operator_group, state.consecutive_failures,
                 state.consecutive_successes, state.last_seen or now,
                 state.last_probe, state.last_success, endpoint_id))
            changed = (previous is None
                       or previous["availability"] != state.availability.value
                       or previous["security"] != state.security.value)
            if changed:
                self.connection.execute(
                    "INSERT INTO classification_history (endpoint_id, changed_at, "
                    "availability, security, reason) VALUES (?,?,?,?,?)",
                    (endpoint_id, now, state.availability.value,
                     state.security.value, state.reason))

    # ------------------------------------------------------------- observations
    def record_probe(self, result: ProbeResult, *, now: Optional[int] = None) -> None:
        now = int(now if now is not None else time.time())
        endpoint_id = self.endpoint_id(result.endpoint)
        if endpoint_id is None:
            endpoint_id = self.upsert_endpoint(result.endpoint, now=now)
        with self.connection:
            self.connection.execute(
                "INSERT INTO observations (endpoint_id, observed_at, reachable, "
                "error_category, server_version, height, tip_hash, rpc_latency_ms, "
                "backend_json, vantage_point) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (endpoint_id, now, 1 if result.reachable else 0,
                 result.error_category, result.server_version, result.height,
                 result.tip_hash, result.rpc_latency_ms,
                 json.dumps(result.backend) if result.backend else None,
                 result.vantage_point))
            for family, addresses in (("ipv4", result.resolved_ipv4),
                                      ("ipv6", result.resolved_ipv6)):
                for address in addresses:
                    self.connection.execute(
                        "INSERT INTO addresses (endpoint_id, family, address, "
                        "first_seen, last_seen) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(endpoint_id, family, address) "
                        "DO UPDATE SET last_seen=excluded.last_seen",
                        (endpoint_id, family, address, now, now))
            if result.tls_fingerprint:
                self.connection.execute(
                    "INSERT INTO certificates (endpoint_id, fingerprint, issuer, "
                    "not_after, first_seen, last_seen) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(endpoint_id, fingerprint) "
                    "DO UPDATE SET last_seen=excluded.last_seen",
                    (endpoint_id, result.tls_fingerprint, result.tls_issuer,
                     result.tls_not_after, now, now))

    def record_peer_edge(self, source: EndpointId, target: EndpointId,
                         *, now: Optional[int] = None) -> None:
        """Remember who announced whom.  A peer edge is provenance, not trust."""
        now = int(now if now is not None else time.time())
        source_id = self.upsert_endpoint(source, now=now)
        target_id = self.upsert_endpoint(target, source=DiscoverySource.GOSSIP, now=now)
        with self.connection:
            self.connection.execute(
                "INSERT INTO peer_edges (source_id, target_id, first_seen, last_seen) "
                "VALUES (?,?,?,?) ON CONFLICT(source_id, target_id) DO UPDATE SET "
                "last_seen=excluded.last_seen, announcements=announcements+1",
                (source_id, target_id, now, now))

    def addresses_for(self, endpoint: EndpointId) -> List[sqlite3.Row]:
        endpoint_id = self.endpoint_id(endpoint)
        if endpoint_id is None:
            return []
        return self.connection.execute(
            "SELECT family, address, first_seen, last_seen FROM addresses "
            "WHERE endpoint_id=? ORDER BY family, address", (endpoint_id,)).fetchall()

    def peer_edges(self) -> List[sqlite3.Row]:
        return self.connection.execute(
            "SELECT s.hostname AS source_host, s.port AS source_port, "
            "t.hostname AS target_host, t.port AS target_port, e.announcements "
            "FROM peer_edges e JOIN endpoints s ON s.id=e.source_id "
            "JOIN endpoints t ON t.id=e.target_id").fetchall()

    # ------------------------------------------------------------- policy state
    def load_minimum_policy_version(self) -> int:
        """The lowest signed safe-Core policyVersion this monitor will still
        accept.  0 until a policy has ever been verified and accepted."""
        row = self.connection.execute(
            "SELECT minimum_policy_version FROM policy_state WHERE id=1").fetchone()
        return row["minimum_policy_version"] if row else 0

    def record_policy_version(self, version: int) -> None:
        """Raise the anti-rollback high-water mark after a policy verifies.

        Never moves backwards: a lower version is silently ignored rather
        than lowering the bar for the next run.
        """
        with self.connection:
            self.connection.execute(
                "INSERT INTO policy_state (id, minimum_policy_version) VALUES (1, ?) "
                "ON CONFLICT (id) DO UPDATE SET minimum_policy_version = "
                "MAX(minimum_policy_version, excluded.minimum_policy_version)",
                (version,))

    # ------------------------------------------------------------ conflict state
    def record_conflict(self, group_key: str, *, now: Optional[int] = None) -> int:
        """Record that ``group_key`` conflicted with the chain evidence on
        this crawl, and return the resulting cross-crawl confirmation count.

        The first observation inserts a row with confirmations=1
        (CONFLICT_SUSPECTED); a later, independent crawl that calls this
        again for the same group increments it (eventually reaching
        CHAIN_CONFLICT once it meets Thresholds.conflict_confirmations).
        Called at most once per group per run_discovery() call, so
        re-processing the same crawl's response twice never double-counts.
        """
        now = int(now if now is not None else time.time())
        with self.connection:
            self.connection.execute(
                "INSERT INTO chain_conflicts (group_key, confirmations, first_seen, "
                "last_seen) VALUES (?, 1, ?, ?) ON CONFLICT (group_key) DO UPDATE SET "
                "confirmations = confirmations + 1, last_seen = excluded.last_seen",
                (group_key, now, now))
            row = self.connection.execute(
                "SELECT confirmations FROM chain_conflicts WHERE group_key=?",
                (group_key,)).fetchone()
        return row["confirmations"]

    def clear_conflict(self, group_key: str) -> None:
        """Drop any persisted conflict confirmations for ``group_key``.

        Recovery: call only once a crawl has positively verified that
        group's chain evidence again, never merely because it was not
        seen conflicting this time -- absence of a fresh conflict is not
        evidence the earlier one was resolved.
        """
        with self.connection:
            self.connection.execute(
                "DELETE FROM chain_conflicts WHERE group_key=?", (group_key,))

    def conflict_confirmations(self, group_key: str) -> int:
        """How many independent crawls have confirmed this group conflicting.
        0 if there is no current suspicion."""
        row = self.connection.execute(
            "SELECT confirmations FROM chain_conflicts WHERE group_key=?",
            (group_key,)).fetchone()
        return row["confirmations"] if row else 0

    # ---------------------------------------------------------------- retention
    def prune(self, *, keep_observation_days: int = 7,
              now: Optional[int] = None) -> int:
        """Drop raw observations older than the retention window, and any
        conflict suspicion that has not been reconfirmed in that same
        window (bounded state: a group an attacker can no longer reach
        does not accumulate confirmation credit forever)."""
        now = int(now if now is not None else time.time())
        cutoff = now - keep_observation_days * 86400
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM observations WHERE observed_at < ?", (cutoff,))
            self.connection.execute(
                "DELETE FROM chain_conflicts WHERE last_seen < ?", (cutoff,))
        return cursor.rowcount
