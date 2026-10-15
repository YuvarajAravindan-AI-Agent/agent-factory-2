# Loan pre-qualification API (synthetic spec)

target: python-fastapi

All data in this service is synthetic. No real applicant, account or bureau data.

## Purpose
A small HTTP service that records consumer loan pre-qualification requests and returns a
rule-based indicative decision. It never contacts a credit bureau.

## Endpoints
- `POST /applications` body: `applicant_ref` (string, `A` + 7 digits), `annual_income` (integer,
  1 to 10,000,000), `requested_amount` (integer, 500 to 1,000,000), `term_months` (integer, 6 to
  84), `monthly_debt` (integer, 0 to 1,000,000). Returns 201 with `{id, status, decision}`; 422 on
  validation failure.
- `GET /applications/{id}` returns the stored application with its decision; 404 if unknown.
- `GET /applications` returns all applications, newest first.

## Rules
- Ids are server-generated UUIDs.
- Monthly payment is `requested_amount / term_months` (no interest in this indicative model).
- Debt-to-income ratio = `(monthly_debt + monthly_payment) / (annual_income / 12)`.
- Decision `approve` when ratio <= 0.36; `refer` when 0.36 < ratio <= 0.45; `decline` above 0.45.
- `status` is always `decided`. A decision is final and is never recomputed.
- Never write applicant_ref or any amount to application logs.

## Accepted limits (the Reviewer must list these as accepted, not as findings)
- Storage is in memory and lost on restart.
- No authentication in this version.
- No interest, fees or credit-bureau inputs.

## Tests required
pytest tests covering each endpoint, all three decision bands and their boundaries, and
validation failures.
