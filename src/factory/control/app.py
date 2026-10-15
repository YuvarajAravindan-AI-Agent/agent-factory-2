"""FastAPI control service. Only /approve/* is meant to be public (behind Caddy)."""

from __future__ import annotations

import html
from collections.abc import Callable

from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.responses import HTMLResponse

from .approval import PgApproval

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>Approve {run}</title><body style="font-family:sans-serif;max-width:36rem;margin:3rem auto">
<h1>Approve generated codebase?</h1><p>Run <b>{run}</b></p>
<p>Package SHA-256:<br><code>{sha}</code></p>{body}</body>"""

FORM = """<form method=post action="/approve/{run}">
<input type=hidden name=t value="{t}">
<button name=decision value=approved>Approve</button>
<button name=decision value=rejected>Reject</button></form>"""


def create_app(approval: PgApproval, resume: Callable[[str, str], None]) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def page(run: str, sha: str, body: str, code: int = 200) -> HTMLResponse:
        return HTMLResponse(
            PAGE.format(run=html.escape(run), sha=html.escape(sha), body=body),
            status_code=code,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    def check(run_id: str, t: str) -> str:
        sha = approval.package_sha(run_id)
        if sha is None or not approval.verify_token(run_id, t):
            raise HTTPException(404, "not found")  # same answer for unknown run and bad token
        return sha

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/approve/{run_id}", response_class=HTMLResponse)
    def confirm(run_id: str, t: str = Query(...)) -> HTMLResponse:
        sha = check(run_id, t)  # GET never changes state, so link scanners cannot approve
        st = approval.status(run_id)
        if st.decision != "pending":
            return page(run_id, sha, f"<p>Already <b>{html.escape(st.decision)}</b>.</p>")
        return page(run_id, sha, FORM.format(run=html.escape(run_id), t=html.escape(t)))

    @app.post("/approve/{run_id}", response_class=HTMLResponse)
    def decide(run_id: str, t: str = Form(...), decision: str = Form(...)) -> HTMLResponse:
        sha = check(run_id, t)
        if decision not in ("approved", "rejected"):
            raise HTTPException(400, "bad decision")
        before = approval.status(run_id).decision
        st = approval.decide(run_id, decision, approval.approver_email)
        if before == "pending" and st.decision in ("approved", "rejected"):
            resume(run_id, st.decision)
        elif st.decision == "expired":  # lapsed before or during the click
            resume(run_id, "expired")
        return page(run_id, sha, f"<p>Recorded: <b>{html.escape(st.decision)}</b>.</p>")

    return app
