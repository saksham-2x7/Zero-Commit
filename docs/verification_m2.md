# Verification Results (M2) — Hamza, Core

## Context

- **Stack URL**: https://d4uzp0bsghs2h.cloudfront.net
- **Region**: `ap-southeast-2` (per spec, commit `0e34a1d`)
- **Stage**: `shared`
- **Date**: 2026-09-19
- **Log groups**: `/aws/lambda/deadman-{apply,confirm,revert,status}-<stage>-<random suffix>` (read off console)
- **Test SG**: tagged `deadman:managed=true` and `deadman:stage=<stage>` (both required; missing either → 403 `SG_NOT_MANAGED`)
- **Auth**: `Authorization: Bearer <token>` (token shared out-of-band, not in repo)

## Offline / mocked results (pre-deploy)

> These prove the code agrees with itself and that request shapes are valid — **not** that it works on real AWS.

| Check | Result |
|---|---|
| Packaging: `from apply/confirm/revert/status.app import handler` from inside `src/` | PASS |
| Third-party imports: none (stdlib + boto3/botocore only) | PASS |
| ULID `change_id` generation: stdlib-only (`os.urandom` + Crockford Base32), 26 chars, unique | PASS |
| Unit suite (`python -m pytest src/tests -q`) | 64 passed (mocks; they prove the code agrees with itself, not that it works on AWS) |

## Live results on deployed stack (M4 walkthrough, Sat 19 Sep 2026, ap-southeast-2)

Run by M4 through the website on the deployed CloudFront stack:

| Flow | Observed Result | Status |
|---|---|---|
| **Apply** | Cut the port-80 rule and the demo server went dark. | PASS |
| **Timeout auto-revert** | Worked. Ended `REVERTED` with trigger `SCHEDULE` (approx 94 s from Apply to REVERTED on a 60 s TTL). | PASS |
| **Confirm** | Worked. Applied change reached `CONFIRMED` status, schedule deleted, rule persisted. | PASS |
| **Manual revert** | Worked. Triggered via UI button, ended `REVERTED` with trigger `MANUAL` ~5 s after Apply. | PASS |
| **Auth check** | Request without token returned 401 Unauthorized; invalid request returned 400. | PASS |

## NOT RUN

- **Check 1h**: Revert latency through CloudFront (10 calls, p95 < 10 s).
- **Check 1a**: Formal EventBridge Scheduler timing verification matrix (`fire_ts - at_ts` in -1..15 s).
- **S1–S10 scenario suite** (`tests/scenarios/run.py`): Not run against the live stack.