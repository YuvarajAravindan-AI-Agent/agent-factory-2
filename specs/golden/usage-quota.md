# Mobile data usage and quota API (synthetic spec)

target: python-fastapi

All data in this service is synthetic. No real subscriber, MSISDN or location data.

## Purpose
A small HTTP service that records data usage events for subscribers and reports remaining
quota against a plan.

## Endpoints
- `POST /subscribers` body: `subscriber_ref` (string, `S` + 8 digits), `plan` (`basic`, `plus` or
  `max`). Returns 201 with the subscriber and its monthly quota in MB; 409 if the reference
  already exists; 422 on validation failure.
- `POST /subscribers/{ref}/usage` body: `megabytes` (integer, 1 to 100000). Adds usage. Returns
  200 with `{used_mb, remaining_mb, status}`; 404 if the subscriber is unknown; 422 on validation
  failure.
- `GET /subscribers/{ref}/quota` returns `{plan, quota_mb, used_mb, remaining_mb, status}`; 404 if
  unknown.
- `POST /subscribers/{ref}/reset` sets used_mb to 0 (billing-cycle rollover). 200; 404 if unknown.

## Rules
- Monthly quotas: `basic` 5000 MB, `plus` 20000 MB, `max` 100000 MB.
- `status` is `ok` below 80% of quota, `warning` from 80% up to and including 100%, and
  `exhausted` when used_mb exceeds quota_mb. Usage over quota is still recorded.
- `remaining_mb` never goes below 0.
- Never write subscriber_ref to application logs.

## Accepted limits (the Reviewer must list these as accepted, not as findings)
- Storage is in memory and lost on restart.
- No authentication in this version.
- No concurrency control beyond a single process.

## Tests required
pytest tests covering each endpoint, the status thresholds and their boundaries, and validation
failures.
