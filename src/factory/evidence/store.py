"""Evidence directory + hash-chained audit table (SPEC section 10)."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

GENESIS = "0" * 64
RUN_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


def sha256(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def row_hash(prev: str, run_id: str, ts: str, actor: str, action: str, payload_hash: str) -> str:
    return sha256("|".join([prev, run_id, ts, actor, action, payload_hash]))


class FsEvidence:
    """runs/<runId>/... on disk. Paths are validated; nothing escapes the run directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, run_id: str, rel: str) -> Path:
        if not re.match(RUN_ID_RE, run_id):
            raise ValueError(f"bad run id: {run_id!r}")
        base = (self.root / run_id).resolve()
        p = (base / rel).resolve()
        if p == base or base not in p.parents:
            raise ValueError(f"path escapes run directory: {rel!r}")
        return p

    def write(self, run_id: str, rel_path: str, content: str) -> str:
        p = self._path(run_id, rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return sha256(content)

    def read(self, run_id: str, rel_path: str) -> str:
        return self._path(run_id, rel_path).read_text()

    def package(self, run_id: str, files: dict[str, str]) -> str:
        """Write package.zip (deterministic) and SHA256SUMS; return the zip's SHA-256."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(files):
                zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                zi.external_attr = 0o644 << 16
                z.writestr(zi, files[name])
        data = buf.getvalue()
        p = self._path(run_id, "package.zip")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        digest = sha256(data)
        sums = [f"{sha256(files[n])}  workspace/{n}" for n in sorted(files)]
        sums.append(f"{digest}  package.zip")
        self.write(run_id, "SHA256SUMS", "\n".join(sums) + "\n")
        return digest

    def verify_sums(self, run_id: str) -> bool:
        """Re-check package.zip against SHA256SUMS."""
        sums = self.read(run_id, "SHA256SUMS").splitlines()
        want = next(line.split()[0] for line in sums if line.endswith("package.zip"))
        return sha256(self._path(run_id, "package.zip").read_bytes()) == want


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  seq bigserial PRIMARY KEY,
  run_id text NOT NULL, ts text NOT NULL, actor text NOT NULL, action text NOT NULL,
  payload_hash text NOT NULL, prev_hash text NOT NULL, row_hash text NOT NULL
);
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log;
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();
CREATE TABLE IF NOT EXISTS approvals (
  run_id text PRIMARY KEY, package_sha256 text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
  decision text NOT NULL DEFAULT 'pending', approver text, decided_at timestamptz
);
"""


def setup_schema(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(SCHEMA)


def grant_app_role(admin_dsn: str, role: str, password: str) -> None:
    """Create/refresh the restricted application role. It may append to audit_log but has no
    UPDATE/DELETE/TRUNCATE, cannot create objects, and cannot touch other tables."""
    ident = sql.Identifier(role)
    with psycopg.connect(admin_dsn, autocommit=True) as c:
        exists = c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        c.execute(
            sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                ident, sql.Literal(password)
            )
        )
        c.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(ident))
        c.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(ident))
        c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
        c.execute(sql.SQL("GRANT SELECT, INSERT ON audit_log TO {}").format(ident))
        c.execute(sql.SQL("GRANT USAGE ON SEQUENCE audit_log_seq_seq TO {}").format(ident))
        c.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE ON approvals TO {}").format(ident))
        for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            if c.execute("SELECT to_regclass(%s)", (f"public.{t}",)).fetchone()[0]:  # type: ignore[index]
                c.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(
                        sql.Identifier(t), ident
                    )
                )
        if c.execute("SELECT to_regclass('public.checkpoint_migrations')").fetchone()[0]:  # type: ignore[index]
            c.execute(sql.SQL("GRANT SELECT ON checkpoint_migrations TO {}").format(ident))


class PgAudit:
    """Append-only, hash-chained audit table. UPDATE/DELETE/TRUNCATE raise (trigger);
    in production also revoke those privileges from the application role."""

    def __init__(self, dsn: str, clock: Callable[[], datetime] | None = None) -> None:
        self.dsn = dsn
        self.clock = clock or (lambda: datetime.now(UTC))

    def audit(self, run_id: str, actor: str, action: str, payload: str) -> str:
        ts = self.clock().isoformat()
        ph = sha256(payload)
        with psycopg.connect(self.dsn) as c, c.transaction():
            c.execute("SELECT pg_advisory_xact_lock(7001)")
            last = c.execute("SELECT row_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last[0] if last else GENESIS
            rh = row_hash(prev, run_id, ts, actor, action, ph)
            c.execute(
                "INSERT INTO audit_log"
                " (run_id, ts, actor, action, payload_hash, prev_hash, row_hash)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (run_id, ts, actor, action, ph, prev, rh),
            )
        return rh

    def rows(self, run_id: str | None = None) -> list[dict[str, Any]]:
        q = (
            "SELECT seq, run_id, ts, actor, action, payload_hash, prev_hash, row_hash"
            " FROM audit_log"
        )
        args: tuple[str, ...] = ()
        if run_id:
            q += " WHERE run_id = %s"
            args = (run_id,)
        with psycopg.connect(self.dsn) as c:
            cur = c.execute(q + " ORDER BY seq", args)
            cols = [d.name for d in cur.description or []]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def verify_chain(self) -> bool:
        return self.first_break() is None

    def first_break(self) -> int | None:
        prev = GENESIS
        for r in self.rows():
            want = row_hash(prev, r["run_id"], r["ts"], r["actor"], r["action"], r["payload_hash"])
            if r["prev_hash"] != prev or r["row_hash"] != want:
                return int(r["seq"])
            prev = r["row_hash"]
        return None


class Evidence:
    """EvidencePort: files on disk, audit rows in Postgres."""

    def __init__(self, fs: FsEvidence, audit: PgAudit) -> None:
        self.fs, self._audit = fs, audit

    def write(self, run_id: str, rel_path: str, content: str) -> str:
        return self.fs.write(run_id, rel_path, content)

    def read(self, run_id: str, rel_path: str) -> str:
        return self.fs.read(run_id, rel_path)

    def audit(self, run_id: str, actor: str, action: str, payload: str) -> str:
        return self._audit.audit(run_id, actor, action, payload)

    def verify_chain(self) -> bool:
        return self._audit.verify_chain()

    def package(self, run_id: str, files: dict[str, str]) -> str:
        return self.fs.package(run_id, files)
