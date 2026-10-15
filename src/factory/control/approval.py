"""Human approval: signed link, confirmation page (GET changes nothing), expiry, one decision."""

from __future__ import annotations

import hashlib
import hmac
import smtplib
import ssl
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Protocol

import psycopg

from factory.ports.models import ApprovalDecision

DEFAULT_TTL = timedelta(hours=24)


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class SmtpMailer:
    """Credentials come from the caller (environment); they are never logged."""

    def __init__(self, host: str, port: int, user: str, password: str, sender: str) -> None:
        self.host, self.port, self.user, self.password, self.sender = (
            host,
            port,
            user,
            password,
            sender,
        )

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        ctx = ssl.create_default_context()
        if self.port == 465:
            with smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=30) as s:
                s.login(self.user, self.password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=30) as s:
                s.starttls(context=ctx)
                s.login(self.user, self.password)
                s.send_message(msg)


def make_token(secret: bytes, run_id: str, package_sha256: str, expires: datetime) -> str:
    msg = f"{run_id}|{package_sha256}|{int(expires.timestamp())}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


class PgApproval:
    """ApprovalPort over the `approvals` table. The link token is an HMAC, so the database
    never holds anything that could approve a run."""

    def __init__(
        self,
        dsn: str,
        secret: bytes,
        base_url: str,
        approver_email: str,
        mailer: Mailer,
        ttl: timedelta = DEFAULT_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("approval secret must be at least 32 bytes")
        self.dsn, self.secret, self.base_url = dsn, secret, base_url.rstrip("/")
        self.approver_email, self.mailer, self.ttl = approver_email, mailer, ttl
        self.clock = clock or (lambda: datetime.now(UTC))

    def _row(self, run_id: str) -> tuple[str, datetime, str, str | None, datetime | None] | None:
        with psycopg.connect(self.dsn) as c:
            r = c.execute(
                "SELECT package_sha256, expires_at, decision, approver, decided_at"
                " FROM approvals WHERE run_id=%s",
                (run_id,),
            ).fetchone()
        return r

    def request(self, run_id: str, package_sha256: str) -> None:
        expires = self.clock() + self.ttl
        with psycopg.connect(self.dsn) as c, c.transaction():
            c.execute(
                "INSERT INTO approvals (run_id, package_sha256, expires_at) VALUES (%s,%s,%s)"
                " ON CONFLICT (run_id) DO UPDATE SET package_sha256=EXCLUDED.package_sha256,"
                " expires_at=EXCLUDED.expires_at, decision='pending',"
                " approver=NULL, decided_at=NULL",
                (run_id, package_sha256, expires),
            )
        token = make_token(self.secret, run_id, package_sha256, expires)
        link = f"{self.base_url}/approve/{run_id}?t={token}"
        self.mailer.send(
            self.approver_email,
            f"Approve generated codebase {run_id}?",
            f"Run {run_id} passed the gate and review.\nPackage SHA-256: {package_sha256}\n"
            f"Expires: {expires.isoformat()}\n\nOpen to review and confirm (nothing happens "
            f"until you press a button on that page):\n{link}\n",
        )

    def verify_token(self, run_id: str, token: str) -> bool:
        row = self._row(run_id)
        if row is None:
            return False
        want = make_token(self.secret, run_id, row[0], row[1])
        return hmac.compare_digest(want, token)

    def decide(self, run_id: str, decision: str, approver: str) -> ApprovalDecision:
        """Record approved/rejected once; later calls and expired requests change nothing."""
        if decision not in ("approved", "rejected"):
            raise ValueError("decision must be approved or rejected")
        now = self.clock()
        with psycopg.connect(self.dsn) as c, c.transaction():
            c.execute(
                "UPDATE approvals SET decision=%s, approver=%s, decided_at=%s"
                " WHERE run_id=%s AND decision='pending' AND expires_at > %s",
                (decision, approver, now, run_id, now),
            )
        return self.status(run_id)

    def status(self, run_id: str) -> ApprovalDecision:
        row = self._row(run_id)
        if row is None:
            return ApprovalDecision(run_id=run_id, decision="pending")
        _pkg, expires, decision, approver, decided_at = row
        if decision == "pending" and self.clock() >= expires:
            decision = "expired"
        return ApprovalDecision(
            run_id=run_id,
            decision=decision,
            approver=approver,
            decided_at=decided_at.isoformat() if decided_at else None,
        )

    def package_sha(self, run_id: str) -> str | None:
        row = self._row(run_id)
        return row[0] if row else None

    def lapsed_pending(self) -> list[str]:
        """Runs whose request expired without a decision (for the expiry sweep)."""
        with psycopg.connect(self.dsn) as c:
            rows = c.execute(
                "SELECT run_id FROM approvals WHERE decision='pending' AND expires_at <= %s",
                (self.clock(),),
            ).fetchall()
        return [r[0] for r in rows]
