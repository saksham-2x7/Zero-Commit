## v1.1 CHANGES (Saksham owns this file)
1. Packaging: all Lambdas use CodeUri `src/` and Handler `<fn>.app.handler` (e.g. `apply.app.handler`) so `common` is importable. Supersedes "handler app.handler" in §2.
2. `src/tests/**` (unit tests with moto/stubs) is owned by M2. `tests/**` (scenario runner) stays with M4.
3. Verification results: docs/verification_m1.md (M1: 1a, 1b, 1e, 1g), docs/verification_m2.md (M2: 1h), docs/verification_m4.md (M4: 1c, 1d, 1f).
4. Test EC2 user-data is inlined in infra/template.yaml (M1): serve HTTP 200 on port 80, body includes hostname. M4 owns demo/** (curl_loop.sh etc.).
5. Frontend: Vite + React, static build to web/dist, no external CDNs or web fonts, web/config.json = {"apiBase":"/api"}. M1's scripts/deploy_web.sh builds and syncs it.
6. README.md and .gitignore: M1.

# MASTER SPEC v1.0

## §1 Assumptions
ASSUMPTION-1: EventBridge Scheduler `at()` fires within ~5 seconds of the target time (to be verified).
ASSUMPTION-2: Security Group modifications take effect almost instantly.

## §2 Infrastructure
* API Gateway (HTTP API) with Lambda authorizer.
* 4 API Routes under `/api`:
  * `POST /api/changes` -> apply Lambda
  * `GET /api/changes/{id}` -> status Lambda
  * `POST /api/changes/{id}/confirm` -> confirm Lambda
  * `POST /api/changes/{id}/revert` -> revert Lambda (manual revert)
* 5 Lambdas total (authorizer, apply, status, confirm, revert). Timeout: apply 15s, revert 25s.
* DynamoDB Table: `pk` (String), TTL attribute `purge_at`.
* EventBridge Scheduler Group: `deadman-<stage>`.

## §3 Config/Env
* `Stage`: regex `^(shared|m[1-4])$`
* `AuthToken`: NoEcho
* `MinTtlSeconds`: Minimum TTL allowed for a change

## §4 State transitions & Rule reconciliation
* `PENDING`: Initial state after applying change and arming scheduler.
* `CONFIRMED`: Target state when human confirms.
* `REVERTING`: Intermediate state during revert.
* `REVERTED`: Final state when revert succeeds completely.
* `PARTIAL_REVERT`: Revert succeeded partially.
* `FAILED`: Revert or apply failed completely.

## §5 API Contract & Errors
Endpoints return standard JSON HTTP codes.
Errors:
* 401 UNAUTHORIZED: Missing/invalid token.
* 403 SG_NOT_MANAGED: Target SG is not managed by Deadman.
* 404 NOT_FOUND: Change ID not found.
* 409 WINDOW_EXPIRED: Attempt to confirm after TTL expired.

## §6 IAM Permissions
* Apply Lambda: `ec2:DescribeSecurityGroupRules`, `ec2:RevokeSecurityGroupIngress`, `dynamodb:PutItem`, `scheduler:CreateSchedule`, `iam:PassRole`.
* Revert Lambda: `ec2:AuthorizeSecurityGroupIngress`, `dynamodb:GetItem`, `dynamodb:UpdateItem`.
* Confirm Lambda: `scheduler:DeleteSchedule`, `dynamodb:UpdateItem`.
* Scheduler Execution Role: `lambda:InvokeFunction` on Revert Lambda only.

## §7 Repo Tree
infra/
scripts/
src/authorizer/
src/apply/
src/confirm/
src/revert/
src/status/
src/common/
src/tests/
tests/
web/
docs/
probes/
demo/
