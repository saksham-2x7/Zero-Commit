# Verification Results (M2) — Hamza, Core

## Context

- **Stack URL**: https://d4uzp0bsghs2h.cloudfront.net
- **Region**: `ap-southeast-2` (per spec, commit `0e34a1d`)
- **Stage**: `shared`
- **Date**: 2026-09-19
- **Log groups**: `/aws/lambda/deadman-{apply,confirm,revert,status}-<stage>-<random suffix>` (read off console)
- **Test SG**: tagged `deadman:managed=true` and `deadman:stage=<stage>` (both required; missing either → 403 `SG_NOT_MANAGED`)
- **Auth**: `Authorization: Bearer <token>` (token shared out-of-band, not in repo)

## Offline / mocked checks (pre-deploy, all PASS)

> These prove the code agrees with itself and that request shapes are valid — **not** that it works on real AWS. Live behaviour is verified in the flows below.

| Check | Result |
|---|---|
| Packaging: `from apply/confirm/revert/status.app import handler` from inside `src/` | PASS |
| Third-party imports: none (stdlib + boto3/botocore only) | PASS |
| ULID `change_id` generation: stdlib-only (`os.urandom` + Crockford Base32), 26 chars, unique | PASS |
| Unit suite (`python -m pytest src/tests -q`) | 63 passed (including post-T0 crash-safety catch-all, SGLOCK release, error formatting, env fail-fast) |

## Live smoke test (deployed stack, PASS)

Run by M4, Sat 19 Sep 2026 ~12:13 GMT, through CloudFront, region `ap-southeast-2`.

| Call | Result | Evidence |
|---|---|---|
| `POST /api/changes`, no token | 401 `{"message":"Unauthorized"}` | API Gateway request ID `D8hcQixcSwMEJkQ=` |
| `POST /api/changes`, valid token | 400 `INVALID_REQUEST` `"sg_id must be a valid security group ID"` — reached apply Lambda | API Gateway request ID `D8hcZgGQywMEPSA=` |

Shows the Lambda authorizer and the apply handler are live on the deployed stack. (Bearer token never written to this file or any commit.)

## Live flows (run order: manual revert → confirm → timeout)

Each flow records `change_id`, timestamps, and an honest PASS/FAIL with evidence.

### 1. Manual revert (fastest)

- [ ] `POST /api/changes` → apply response: statusCode, `change_id`, `expires_at`
- [ ] `POST /api/changes/{id}/revert` → revert response: statusCode, status
- [ ] Expect: 200 `REVERTED`, schedule `dm-<id>` deleted, DDB status `REVERTED`
- [ ] CloudWatch: revert log shows manual path (`requestContext.http`), no `delete_schedule` error

**Result: PENDING (not run — waiting on sg- ID + token)**

### 2. Confirm

- [ ] `POST /api/changes` → apply response: statusCode, `change_id`, `expires_at`
- [ ] `POST /api/changes/{id}/confirm` → confirm response: statusCode, status
- [ ] Expect: 200 `CONFIRMED`, schedule `dm-<id>` deleted, DDB status `CONFIRMED`, change persists
- [ ] CloudWatch: confirm log shows T2 + `delete_schedule`

**Result: PENDING (not run — waiting on sg- ID + token)**

### 3. Timeout (auto-revert) + scheduler timing (spec check 1a)

- [ ] `POST /api/changes` → apply response: statusCode, `change_id`, **`expires_at`** (record exactly)
- [ ] No confirm; wait past `expires_at`
- [ ] CloudWatch: revert log line timestamp (`trigger=SCHEDULE`) — record exactly
- [ ] **Timing: `revert_log_ts − expires_at` must be ≥ 0 (never early) and ≤ 15 s (bounded delay)** — spec 1a
- [ ] Expect: 200 `REVERTED`, schedule self-deleted (`ActionAfterCompletion=DELETE`), DDB status `REVERTED`

**Result: PENDING (not run — waiting on sg- ID + token)**

## Red flags (any → FAIL + investigate)

- `SCHEDULE_FAILED` (502) — schedule creation failed
- `APPLY_FAILED_REVERTED` / `APPLY_FAILED_REVERT_INCOMPLETE` — cut failed
- `SG_NOT_MANAGED` (403) — test SG missing/wrong tags
- `Traceback` in any Lambda log
- Revert never fires at expiry (Scheduler `at()` seconds issue — spec 1a fallback)
- Revert fires **early** (before `expires_at`)