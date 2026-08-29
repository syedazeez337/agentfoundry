"""Control plane: SQLite.

Invariant: this database holds nothing that cannot be rebuilt by rescanning the
evidence plane. `af reindex` must reconstruct it from bundles alone. That makes
the DB disposable and corruption a non-event.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

from af.util import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS architecture(
  hash TEXT PRIMARY KEY, name TEXT, manifest TEXT, base_hash TEXT,
  operators TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS task(
  id TEXT PRIMARY KEY, hash TEXT, suite TEXT, path TEXT, difficulty REAL,
  category TEXT, admissible INTEGER, validated_at TEXT);

CREATE TABLE IF NOT EXISTS trial(
  trial_key TEXT PRIMARY KEY, trial_spec_hash TEXT, task_id TEXT,
  arch_hash TEXT, nonce INTEGER, state TEXT, worker TEXT, lease_until REAL,
  bundle_path TEXT, experiment_hash TEXT, arm_id TEXT,
  created_at TEXT, sealed_at TEXT, error TEXT);

-- Experiment membership is a join, not a column on `trial`.
-- A trial_key is globally unique evidence and can legitimately belong to more
-- than one experiment (two designs sharing a baseline arm). Putting the
-- experiment on the trial row made membership 1:1 and silently dropped the
-- shared arm from whichever experiment ran second.
CREATE TABLE IF NOT EXISTS experiment_trial(
  experiment_hash TEXT, trial_key TEXT, arm_id TEXT,
  PRIMARY KEY(experiment_hash, trial_key));

CREATE TABLE IF NOT EXISTS score(
  trial_key TEXT, analysis_version TEXT, task_id TEXT, arch_hash TEXT,
  outcomes TEXT, flags TEXT, credibility TEXT, detail TEXT, graded_at TEXT,
  PRIMARY KEY(trial_key, analysis_version));

CREATE TABLE IF NOT EXISTS experiment(
  hash TEXT PRIMARY KEY, name TEXT, spec TEXT, state TEXT, frozen_at TEXT,
  budget TEXT, plan TEXT);

CREATE TABLE IF NOT EXISTS verdict(
  hash TEXT PRIMARY KEY, experiment_hash TEXT, bundle_set_hash TEXT,
  analysis_version TEXT, result TEXT, payload TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS finding(
  id TEXT PRIMARY KEY, kind TEXT, label TEXT, evidence TEXT,
  confidence REAL, calibration TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS archive(
  arch_hash TEXT PRIMARY KEY, niche TEXT, score REAL, cost REAL,
  payload TEXT, created_at TEXT);

CREATE INDEX IF NOT EXISTS idx_trial_state ON trial(state);
CREATE INDEX IF NOT EXISTS idx_trial_exp ON trial(experiment_hash);
CREATE INDEX IF NOT EXISTS idx_score_task ON score(task_id);
"""


class Store:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------- helpers

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def q(self, sql: str, *args) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, args))

    def one(self, sql: str, *args) -> sqlite3.Row | None:
        rows = self.q(sql, *args)
        return rows[0] if rows else None

    # -------------------------------------------------------- architectures

    def put_architecture(self, arch) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO architecture VALUES (?,?,?,?,?,?)",
            (arch.hash, arch.name, json.dumps(arch.to_dict()), arch.base_hash,
             json.dumps(list(arch.operators)), now_iso()),
        )
        return arch.hash

    def get_architecture(self, hash_or_prefix: str) -> dict | None:
        r = self.one("SELECT * FROM architecture WHERE hash=?", hash_or_prefix)
        if r is None:
            r = self.one("SELECT * FROM architecture WHERE hash LIKE ?",
                         hash_or_prefix + "%")
        if r is None:
            r = self.one("SELECT * FROM architecture WHERE name=?", hash_or_prefix)
        return dict(r) if r else None

    def list_architectures(self) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM architecture ORDER BY created_at")]

    # ---------------------------------------------------------------- tasks

    def put_task(self, task, admissible: bool | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO task VALUES (?,?,?,?,?,?,?,?)",
            (task.id, task.hash, task.suite, str(task.dir), task.difficulty,
             task.category, None if admissible is None else int(admissible),
             now_iso() if admissible is not None else None),
        )

    def list_tasks(self, suite: str | None = None) -> list[dict]:
        if suite:
            return [dict(r) for r in self.q("SELECT * FROM task WHERE suite=? ORDER BY id", suite)]
        return [dict(r) for r in self.q("SELECT * FROM task ORDER BY id")]

    # --------------------------------------------------------------- trials

    def enqueue_trial(self, spec, experiment_hash: str | None = None,
                      arm_id: str | None = None) -> str:
        """Record the trial once, and its experiment membership separately.

        INSERT OR IGNORE on `trial` is deliberate: if this exact TrialSpec has
        already been run, that evidence is reused rather than re-paid for. The
        membership row is always written, so a shared arm belongs to every
        experiment that asked for it.
        """
        self.conn.execute(
            "INSERT OR IGNORE INTO trial(trial_key,trial_spec_hash,task_id,arch_hash,"
            "nonce,state,created_at) VALUES (?,?,?,?,?,?,?)",
            (spec.trial_key, spec.hash, spec.task_id, spec.arch_hash, spec.nonce,
             "queued", now_iso()),
        )
        if experiment_hash:
            self.conn.execute(
                "INSERT OR REPLACE INTO experiment_trial VALUES (?,?,?)",
                (experiment_hash, spec.trial_key, arm_id),
            )
        return spec.trial_key

    def lease_trial(self, worker: str, exp_hash: str | None = None,
                    lease_s: int = 3600) -> dict | None:
        import time

        with self.tx() as c:
            if exp_hash:
                row = c.execute(
                    "SELECT t.* FROM trial t "
                    "JOIN experiment_trial et ON et.trial_key = t.trial_key "
                    "WHERE et.experiment_hash = ? AND (t.state='queued' OR "
                    "(t.state='leased' AND t.lease_until < ?)) "
                    "ORDER BY t.created_at LIMIT 1",
                    (exp_hash, time.time()),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT * FROM trial WHERE state='queued' OR "
                    "(state='leased' AND lease_until < ?) ORDER BY created_at LIMIT 1",
                    (time.time(),),
                ).fetchone()
            if row is None:
                return None
            c.execute(
                "UPDATE trial SET state='leased', worker=?, lease_until=? WHERE trial_key=?",
                (worker, time.time() + lease_s, row["trial_key"]),
            )
            return dict(row)

    def release_trial(self, trial_key: str) -> None:
        """Return a trial to the queue, dropping whatever lease it held."""
        self.conn.execute(
            "UPDATE trial SET state='queued', worker=NULL, lease_until=NULL "
            "WHERE trial_key=?", (trial_key,))

    def trials_in_states(self, states: tuple[str, ...],
                         exp_hash: str | None = None) -> list[dict]:
        marks = ",".join("?" for _ in states)
        if exp_hash:
            return [dict(r) for r in self.q(
                f"SELECT t.* FROM trial t "
                f"JOIN experiment_trial et ON et.trial_key = t.trial_key "
                f"WHERE et.experiment_hash=? AND t.state IN ({marks}) "
                f"ORDER BY t.created_at", exp_hash, *states)]
        return [dict(r) for r in self.q(
            f"SELECT * FROM trial WHERE state IN ({marks}) ORDER BY created_at",
            *states)]

    def finish_trial(self, trial_key: str, state: str, bundle_path: str | None = None,
                     error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE trial SET state=?, bundle_path=?, sealed_at=?, error=? WHERE trial_key=?",
            (state, bundle_path, now_iso(), error, trial_key),
        )

    def trials_for_experiment(self, exp_hash: str) -> list[dict]:
        return [dict(r) for r in self.q(
            "SELECT t.*, et.arm_id AS arm_id FROM trial t "
            "JOIN experiment_trial et ON et.trial_key = t.trial_key "
            "WHERE et.experiment_hash=? ORDER BY t.created_at", exp_hash)]

    def counts_by_state(self, exp_hash: str | None = None) -> dict[str, int]:
        if exp_hash:
            rows = self.q(
                "SELECT t.state AS state, COUNT(*) n FROM trial t "
                "JOIN experiment_trial et ON et.trial_key = t.trial_key "
                "WHERE et.experiment_hash=? GROUP BY t.state", exp_hash)
        else:
            rows = self.q("SELECT state, COUNT(*) n FROM trial GROUP BY state")
        return {r["state"]: r["n"] for r in rows}

    # --------------------------------------------------------------- scores

    def put_score(self, score) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO score VALUES (?,?,?,?,?,?,?,?,?)",
            (score.trial_key, score.analysis_version, score.task_id, score.arch_hash,
             json.dumps(score.outcomes), json.dumps(score.flags), score.credibility,
             json.dumps(score.detail), score.graded_at),
        )

    def get_scores(self, trial_keys: list[str], analysis_version: str) -> dict[str, dict]:
        if not trial_keys:
            return {}
        marks = ",".join("?" * len(trial_keys))
        rows = self.q(
            f"SELECT * FROM score WHERE analysis_version=? AND trial_key IN ({marks})",
            analysis_version, *trial_keys)
        out = {}
        for r in rows:
            d = dict(r)
            d["outcomes"] = json.loads(d["outcomes"])
            d["flags"] = json.loads(d["flags"])
            d["detail"] = json.loads(d["detail"])
            out[d["trial_key"]] = d
        return out

    def unscored_trials(self, analysis_version: str) -> list[dict]:
        return [dict(r) for r in self.q(
            "SELECT t.* FROM trial t LEFT JOIN score s "
            "ON s.trial_key=t.trial_key AND s.analysis_version=? "
            "WHERE t.state='sealed' AND s.trial_key IS NULL", analysis_version)]

    # ----------------------------------------------------------- experiments

    def freeze_experiment(self, spec, plan: list[dict]) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO experiment VALUES (?,?,?,?,?,?,?)",
            (spec.hash, spec.name, json.dumps(spec.canonical()), "running",
             now_iso(), json.dumps(spec.budget), json.dumps(plan)),
        )
        return spec.hash

    def get_experiment(self, hash_or_name: str) -> dict | None:
        r = self.one("SELECT * FROM experiment WHERE hash=?", hash_or_name)
        if r is None:
            r = self.one("SELECT * FROM experiment WHERE hash LIKE ?", hash_or_name + "%")
        if r is None:
            r = self.one("SELECT * FROM experiment WHERE name=? ORDER BY frozen_at DESC",
                         hash_or_name)
        return dict(r) if r else None

    def list_experiments(self) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM experiment ORDER BY frozen_at DESC")]

    def set_experiment_state(self, exp_hash: str, state: str) -> None:
        self.conn.execute("UPDATE experiment SET state=? WHERE hash=?", (state, exp_hash))

    # -------------------------------------------------------------- verdicts

    def put_verdict(self, verdict: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO verdict VALUES (?,?,?,?,?,?,?)",
            (verdict["hash"], verdict["experiment_hash"], verdict["bundle_set_hash"],
             verdict["analysis_version"], verdict["result"],
             json.dumps(verdict["payload"]), now_iso()),
        )

    def verdicts_for(self, exp_hash: str) -> list[dict]:
        rows = self.q("SELECT * FROM verdict WHERE experiment_hash=? "
                      "ORDER BY created_at DESC", exp_hash)
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    # -------------------------------------------------------------- findings

    def put_finding(self, f: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO finding VALUES (?,?,?,?,?,?,?)",
            (f["id"], f["kind"], f["label"], json.dumps(f["evidence"]),
             f.get("confidence"), json.dumps(f.get("calibration") or {}), now_iso()))

    def list_findings(self) -> list[dict]:
        out = []
        for r in self.q("SELECT * FROM finding ORDER BY created_at DESC"):
            d = dict(r)
            d["evidence"] = json.loads(d["evidence"])
            d["calibration"] = json.loads(d["calibration"])
            out.append(d)
        return out

    # --------------------------------------------------------------- archive

    def put_archive(self, arch_hash: str, niche: str, score: float, cost: float,
                    payload: dict) -> None:
        cur = self.one("SELECT * FROM archive WHERE niche=?", niche)
        if cur is None or score > cur["score"]:
            self.conn.execute("DELETE FROM archive WHERE niche=?", (niche,))
            self.conn.execute("INSERT OR REPLACE INTO archive VALUES (?,?,?,?,?,?)",
                              (arch_hash, niche, score, cost, json.dumps(payload),
                               now_iso()))

    def list_archive(self) -> list[dict]:
        out = []
        for r in self.q("SELECT * FROM archive ORDER BY niche"):
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out


# ---------------------------------------------------------------- reindex


def reindex(store: Store, bundles_root: Path, tasks_root: Path,
            arch_root: Path) -> dict:
    """Rebuild the control plane from the evidence plane. The invariant."""
    from af.evidence import iter_bundles
    from af.spec.models import ArchitectureSpec, TaskSpec

    counts = {"architectures": 0, "tasks": 0, "trials": 0}
    # A rebuild that silently drops an unreadable file is not a trustworthy
    # rebuild. Skips are counted and named so "reindex succeeded" means
    # something.
    skipped: list[dict] = []

    for p in sorted(Path(arch_root).glob("**/*.yaml")):
        try:
            store.put_architecture(ArchitectureSpec.load(p))
            counts["architectures"] += 1
        except Exception as exc:  # noqa: BLE001
            skipped.append({"kind": "architecture", "path": str(p), "error": str(exc)})

    for p in sorted(Path(tasks_root).glob("**/task.yaml")):
        try:
            store.put_task(TaskSpec.load(p.parent))
            counts["tasks"] += 1
        except Exception as exc:  # noqa: BLE001
            skipped.append({"kind": "task", "path": str(p), "error": str(exc)})

    for b in iter_bundles(bundles_root):
        m = b.manifest
        t = m["trial"]
        store.conn.execute(
            "INSERT OR REPLACE INTO trial(trial_key,trial_spec_hash,task_id,arch_hash,"
            "nonce,state,bundle_path,created_at,sealed_at,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (b.trial_key, t.get("task_hash"), m["task"]["id"],
             m["architecture"]["arch_hash"], t.get("nonce", 0),
             ("sealed" if b.ok else "errored") if b.sealed else "running", str(b.dir),
             m.get("started_at"), b.seal.get("sealed_at"), b.seal.get("error")),
        )
        counts["trials"] += 1

    counts["skipped"] = len(skipped)
    counts["skipped_detail"] = skipped
    return counts
