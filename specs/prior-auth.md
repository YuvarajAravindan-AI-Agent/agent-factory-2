# Prior-authorisation intake API (synthetic spec)

target: python-fastapi

All data in this service is synthetic. No real patient, member or provider data.

## Purpose
A small HTTP service that accepts prior-authorisation requests, validates them, stores them
in memory, and lets a reviewer record a decision.

## Endpoints
- `POST /requests` body: `member_id` (string, `M` + 8 digits), `provider_id` (string, `P` + 6
  digits), `procedure_code` (string, 5 digits), `diagnosis_code` (string, 3-7 chars), `notes`
  (optional string, max 500 chars). Returns 201 with `{id, status: "pending"}`; 422 on
  validation failure.
- `GET /requests/{id}` returns the request and its status; 404 if unknown.
- `POST /requests/{id}/decision` body: `decision` (`approved` or `denied`), `reviewer_id`
  (string). Returns 200 with the updated request; 404 if unknown; 409 if already decided.

## Rules
- Ids are server-generated UUIDs. Status is `pending`, `approved` or `denied`.
- Notes must not be logged. Do not write any identifier to application logs.
- A decision is final; it cannot be changed.

## Accepted limits (the Reviewer must list these as accepted, not as findings)
- Storage is in memory and lost on restart.
- No authentication in this version.

## Tests required
pytest tests covering each endpoint's success and failure paths.
