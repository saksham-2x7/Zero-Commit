## v1.1 CHANGES (Saksham owns this file)

1. Packaging: all Lambdas use CodeUri `src/` and Handler `<fn>.app.handler` (e.g. `apply.app.handler`) so `common` is importable. Supersedes "handler `app.handler`" in §2.
2. `src/tests/**` (unit tests with moto/stubs) is owned by M2. `tests/**` (scenario runner) stays with M4.
3. Verification results: `docs/verification_m1.md` (M1: 1a, 1b, 1e, 1g), `docs/verification_m2.md` (M2: 1h), `docs/verification_m4.md` (M4: 1c, 1d, 1f).
4. Test EC2 user-data is inlined in `infra/template.yaml` (M1): serve HTTP 200 on port 80, body includes hostname. M4 owns `demo/**` (curl_loop etc.).
5. Frontend: Vite + React, static build to `web/dist`, no external CDNs or web fonts, `web/config.json` = `{"apiBase":"/api"}`. M1's `scripts/deploy_web.sh` builds and syncs it.
6. `README.md` and `.gitignore`: M1.

---

# MASTER SPEC v1.0 (frozen; replaces v0.9)

**Status:** verification NOT RUN. Every AWS-behaviour claim is ASSUMPTION-n (A-n) and is tested in §1.
**Locked:** Ship It track. Region `ap-southeast-2`. Python 3.11 + boto3. No Bedrock. AWS only (infra in SAM, frontend on S3 + CloudFront). One shared AWS account. 28 hours total. Scope is security-group IPv4 ingress rules. Timestamps are epoch seconds. `<stage>` is `shared` or `m1`..`m4`.

| M | Person | Role |
|---|---|---|
| 1 | Saksham | Infra |
| 2 | Hamza | Core |
| 3 | Janani | Frontend |
| 4 | Ansh | Data/Test/Demo |

**Changes since v0.9**
1. Added `POST /api/changes/{id}/revert` (manual revert-now). The revert Lambda now serves both Scheduler and API.
2. Shared-account safety: new `deadman:stage` tag with an IAM condition, plus stage rules (§2).
3. Gap fixed: T3 also allows revert when apply never started, which used to leave the change stuck in PENDING.
4. Gap fixed: per-op `applied` flag, so revert never undoes an op Deadman didn't perform.
5. Added judging map, video plan, 28 h milestones, and submission docs (§0).

## 0. PITCH, JUDGING MAP, VIDEO, MILESTONES

**Pitch (locked wording):** "Deadman is a safety net for security-group changes: apply a change, and it undoes itself on a timer unless you confirm it." It is for anyone changing firewall rules remotely: on-call engineers, small teams, students. The other side of the problem is the users of the service that goes dark when a bad rule locks the operator out.

| Criterion (event page) | What we show | Owner |
|---|---|---|
| Idea & impact | One small problem solved end to end: lockout from a bad rule | M4 |
| Built on AWS (architecture and cost decisions count) | Lambda, API Gateway, DynamoDB, EventBridge Scheduler, S3 + CloudFront, IAM conditions. Everything is pay-per-use except the test EC2, which is stopped between sessions. `docs/architecture.md`, `docs/COST.md` (no invented prices). | M1 |
| Learning | Scheduler one-time schedules, PassRole/PassedToService, IAM tag conditions, DynamoDB conditional writes. Each member writes `docs/learnings/mN.md`: what I didn't know before, what I know now. | all |
| Execution | One flow working on the deployed URL: confirm path, timeout path, manual revert | M2, M4 |
| Demo video | 3:00 recorded (no live demo): what it does, who it's for, where AWS fits | M4 |
| UI (design and usability) | Form, countdown, CONFIRM, REVERT NOW, status timeline. Nothing else. | M3 |

**Video plan (3:00, recorded from the deployed CloudFront URL, at least 2 full takes):**
- 0:00–0:25: problem, who it's for, the other side.
- 0:25–1:10: confirm path.
- 1:10–2:10: timeout path. The curl loop dies, the countdown drains, and the auto-revert fires. Then show the Scheduler schedule self-deleted and the DDB diff. Cut in a 5 s REVERT NOW.
- 2:10–2:45: where AWS fits, cost decisions, what we learned.
- 2:45–3:00: close.

**Milestones (hours from now):**

| H | Milestone |
|---|---|
| 0–1 | Run §1 in parallel. M1 posts v1.1 (delta only) at H1. |
| 6 | Shared stack deployed with stubs, mocks live, UI runs on fixtures. |
| 12 | Core flow works in a personal stack. |
| 16 | Integrated on shared stack + CloudFront, including manual revert. |
| **18** | **FEATURE FREEZE.** Bug fixes only. |
| 18–23 | M4 scenario runs, fixes, UI usability pass. |
| 23–26 | Record video, finish learnings, COST, SUBMISSION. |
| 26–28 | Buffer. Submit by H27. No deploys after H26 except blocker fixes. |

## 1. HOUR-1 VERIFICATION CHECKLIST

| # | Owner | Test | Pass/fail | AWS-only fallback |
|---|---|---|---|---|
| 1a | M1 | 10 one-time schedules per T ∈ {45, 60, 90, 120} s, non-minute-aligned, FlexibleTimeWindow OFF, targeting a probe Lambda that logs `fire_ts` (A-1: `at()` honours seconds in ap-southeast-2) | A T passes if 10/10 satisfy −1 ≤ (fire_ts − at_ts) ≤ 15 s (A-2: never early, bounded delay). **Demo TTL = smallest passing T.** `MIN_TTL_SECONDS = max(60, T)`. | Use TTL 180–300 s and cut the video. If seconds aren't honoured or delay exceeds 60 s: Step Functions Standard (`Wait` → revert). |
| 1b | M1 | Apply role has only `scheduler:CreateSchedule` on `schedule/deadman-<stage>/*` (A-4) and `iam:PassRole` on the scheduler role with `iam:PassedToService=scheduler.amazonaws.com` (A-5). Test DynamoDB `TransactWriteItems` with only per-item actions (A-6). | CreateSchedule succeeds with exactly this. It fails with AccessDenied for any other role or group. Record whether GetSchedule and DeleteSchedule are also needed. | Drop the PassedToService condition and keep the role-ARN-scoped PassRole. For DynamoDB, grant what the AccessDenied message names, scoped to the table. |
| 1c | M4 | Authorize tcp/8080 from 203.0.113.0/24, record the id (A). Revoke, re-add the same tuple, record the new id (B). Then: (i) revoke via IpPermissions with a different Description; (ii) poll describe after authorize. | Record A ≠ B (A-9). Match key is `(protocol, from, to, source_type, source)` regardless. (i) rule removed (A-10). (ii) rule visible within 5 s in 20/20 trials (A-11). | (i) fails: revoke by fresh rule id after tuple match, which needs the security-group-rule ARN in IAM (A-12). (ii) fails: poll up to 10 s with backoff. |
| 1d | M4 | For Authorize/RevokeSecurityGroupIngress and DescribeSecurityGroups/Rules, test conditions `aws:ResourceTag/deadman:managed=true` and `aws:ResourceTag/deadman:stage=<stage>` (A-13). | Filled matrix (action → resource-level? tag enforced?). Authorize/Revoke on an SG with a missing or wrong tag returns AccessDenied. Describe* expected to need `"*"` (A-14). | Resource `"*"` plus a mandatory in-code tag check before every mutation and at revert. Document it as a weaker guardrail. |
| 1e | M1 | `ActionAfterCompletion=DELETE`: one schedule targets an OK Lambda, another an always-throwing Lambda. Poll GetSchedule 5 min. Count invocations. | OK schedule gone within 60 s (A-7). Record whether the failing one persists (A-8) and whether duplicates occur (A-3). | Not deleted: confirm and revert call DeleteSchedule explicitly. Deleted despite failure: revert never raises. |
| 1f | M4 | Run the five searches below. | No project does confirm-or-auto-revert on AWS resources. | Reframe the pitch around the closest hit's gap. |
| 1g | M1 | CloudFront `/api/*` with CachingDisabled and AllViewerExceptHostHeader, plus the Lambda authorizer (A-15). | Bearer request returns 200, no-token request returns 401/403. | Call the API URL directly with `DevCors=true`. |
| 1h | M2 | At H6, call `POST /api/changes/{id}/revert` through CloudFront 10 times (A-16: HTTP API integration timeout ≤ 30 s). | p95 < 10 s, never hits 30 s. | Revert returns 202 after T3 and reconciles via async self-invoke (adds `lambda:InvokeFunction` on its own ARN). UI polls. |

**1f queries:**
1. Devpost `"commit confirmed" AWS security group`
2. GitHub `"dead man's switch" security group EventBridge Scheduler revert`
3. Devpost `auto-revert "security group" hackathon AWS`
4. GitHub `"one-time schedule" revoke_security_group_ingress rollback`
5. Google/Product Hunt `confirm or rollback AWS infrastructure change "EventBridge Scheduler"`

## 2. NAMES & CONFIG

| Item | Value |
|---|---|
| Stacks | `deadman-shared` (M1 only), `deadman-dev-m1`..`m4`. Parameter `Stage`, regex `^(shared\|m[1-4])$`. |
| Shared-account rules | Only `shared` creates CloudFront. Only `shared` and `m4` create the test EC2; other stages create a tagged SG only. Stop the EC2 when not testing. |
| Lambdas | `deadman-{apply,confirm,revert,status,authorizer}-<stage>` (handler `app.handler`; see v1.1 change 1) |
| DynamoDB | `deadman-changes-<stage>`, PK `pk` (S), TTL attribute `purge_at` |
| Schedules | Group `deadman-<stage>`, name `dm-<change_id>`, role `deadman-scheduler-<stage>` |
| Schedule target input | `{"trigger":"SCHEDULE","change_id":"<id>"}` |
| Tags on target SGs | `deadman:managed=true`, `deadman:stage=<stage>`. Test SG named `deadman-target-<stage>`. |
| Frontend bucket | `deadman-web-<stage>-<acct>` |
| Routes | `POST /api/changes`, `POST /api/changes/{id}/confirm`, `POST /api/changes/{id}/revert`, `GET /api/changes/{id}`. All use the authorizer. |
| Revert Lambda dispatch | `trigger=="SCHEDULE"` → scheduled path. `requestContext.http` present → manual path (id from path param). Anything else → error. |
| Auth | HTTP API Lambda authorizer (simple response), `Authorization: Bearer <token>`, SAM param `AuthToken` (NoEcho) → env `AUTH_TOKEN`. Known limitation: one shared token. |
| Env vars | `TABLE_NAME, SCHEDULE_GROUP, REVERT_FN_ARN, SCHEDULER_ROLE_ARN, MANAGED_TAG_KEY=deadman:managed, MANAGED_TAG_VALUE=true, STAGE_TAG_KEY=deadman:stage, STAGE, MIN_TTL_SECONDS, MAX_TTL_SECONDS=600, APPLY_LEASE_SECONDS=20, REVERT_LEASE_SECONDS=60` |
| Timeouts | apply 15 s (must stay below the apply lease), revert 25 s (below the revert lease and the HTTP API limit) |

**Decision: serve the API under `/api` on the same CloudFront domain.** This removes CORS and preflight, gives one URL for the frontend and the video, and keeps token handling simple. CloudFront deploys are slow, so only `shared` has one.

## 3. DYNAMODB SCHEMA

**`CHG#<change_id>`** (ULID):
- `status`: `PENDING | CONFIRMED | REVERTING | REVERTED | PARTIAL_REVERT | FAILED`
- `apply_done` (BOOL), `apply_lease_until`, `revert_lease_until`, `revert_owner`
- `revert_trigger`: `SCHEDULE | MANUAL | APPLY_FAILURE`
- `sg_id`, `ttl_seconds` (requested window), `created_at`, `expires_at` (arming time + `ttl_seconds`, also the `at()` time), `schedule_name`
- `snapshot`: all SG ingress rules at arming, each `{rule_id, protocol, from_port, to_port, source_type, source, description}`
- `delta`: `[{op_id, action: AUTHORIZE|REVOKE, protocol, from_port, to_port, cidr, applied: null|true|false}]`. Only *effective* ops are planned (authorize an absent tuple, revoke a present one). `applied` starts null. Apply sets it true, or false if the op was a no-op at cut time (Duplicate/NotFound).
- `confirmed_at`, `reverted_at`, `revert_report` (`[{op_id, result: REVERTED|SKIPPED_ALREADY_SATISFIED|ERROR, detail}]`), `failure_reason`
- `purge_at` = `expires_at` + 604800. This is the DynamoDB TTL attribute and is unrelated to `ttl_seconds`.

**`SGLOCK#<sg_id>`**: `{active_change_id, lock_until}`. One active change per SG.

FAILED means the net didn't complete cleanly (schedule creation failed, or revert hit a precondition error with zero ops done). PARTIAL_REVERT means at least one op succeeded and at least one errored.

## 4. STATE MACHINE

**Order (arm before cut) is kept.** Plan the delta from a fresh describe, then T0 (store snapshot + delta), CreateSchedule, T1 (fence), cut, T1b. Guards are the apply lease, the stored delta, the min TTL, and revert never raising on transient errors. Rejected: cut-then-arm (unprotected change) and reschedule-after-cut (extra failure surface).

| # | From → To | Trigger | ConditionExpression |
|---|---|---|---|
| T0 | ∅ → PENDING | apply, TransactWrite (Put CHG + Put SGLOCK) | CHG: `attribute_not_exists(pk)`. SGLOCK: `attribute_not_exists(pk) OR lock_until < :now`. `lock_until` = `expires_at` + 360. |
| T1 | PENDING (fence) | apply, before cutting | `status=:P AND apply_done=:false AND attribute_not_exists(apply_lease_until)`. Sets `apply_lease_until = now + 20`. |
| T1b | PENDING (cut done) | apply, after ops | `status=:P`. Sets `apply_done=true`. |
| T2 | PENDING → CONFIRMED | confirm | `status=:P AND apply_done=:true AND expires_at > :now`. Then DeleteSchedule (ResourceNotFound OK), release lock. |
| T3 | PENDING → REVERTING | revert (SCHEDULE, MANUAL, APPLY_FAILURE) | `status=:P AND (apply_done=:true OR attribute_not_exists(apply_lease_until) OR apply_lease_until < :now)`. Sets `revert_owner`, `revert_lease_until = now + 60`, `revert_trigger`. |
| T4 | REVERTING → REVERTING | takeover after a crash or retry | `status=:R AND revert_lease_until < :now`. Sets a new owner. |
| T5 | REVERTING → REVERTED | all ops REVERTED or SKIPPED | `status=:R AND revert_owner=:me`. Release lock. |
| T6 | REVERTING → PARTIAL_REVERT | some ops ERROR, some done | same as T5 |
| T7 | REVERTING → FAILED | precondition error, nothing done (SG missing, AccessDenied, tag removed) | same as T5 |
| T8 | PENDING → FAILED | CreateSchedule failed (nothing cut) | `status=:P AND apply_done=:false`. Release lock. |

Manual and APPLY_FAILURE paths also call best-effort DeleteSchedule after T3.

**Per-op revert** (ops in reverse order, fresh describe each; skip any op with `applied == false`):
```
cur = describe_rules(sg_id)
if op.action == "AUTHORIZE":       # undo = revoke
    present(cur, op.tuple) ? revoke_by_tuple(op) : SKIPPED_ALREADY_SATISFIED
else:                              # REVOKE, undo = authorize
    present(cur, op.tuple) ? SKIPPED_ALREADY_SATISFIED
                           : authorize(op)   # Duplicate error → SKIPPED
```

**Edge cases**

| Case | Handling |
|---|---|
| Late confirm | PENDING and `now ≥ expires_at`: 409 `WINDOW_EXPIRED`. Already terminal: 409 `CHANGE_NOT_PENDING`. |
| Double confirm | Second call sees CONFIRMED, returns 200 with `idempotent: true`. |
| Scheduled revert after confirm | T3 fails. Log NOOP, return success. |
| Confirm vs revert race | `status` is the only linearization point. The loser gets ConditionalCheckFailed, re-reads, and returns 409 or 200. |
| Manual revert when T3 fails | REVERTED → 200 `idempotent: true`. REVERTING with live lease → 409 `REVERT_IN_PROGRESS`. CONFIRMED/PARTIAL/FAILED → 409 `CHANGE_NOT_PENDING`. PENDING with live apply lease → 409 `NOT_APPLIED_YET`. |
| Manual vs scheduled revert | Same T3, so one wins. The loser is a NOOP. A leftover schedule fires later as a NOOP or is already deleted. |
| Apply died before T1 | Nothing was cut. T3 allows revert (no lease attribute). Reconcile is all SKIPPED, then REVERTED. |
| Apply loses to a revert | T1 fails, so apply aborts with 409 `CHANGE_NOT_PENDING` and cuts nothing. |
| Op is a no-op at cut time | Mark `applied=false`. Revert skips it. |
| Third-party edit in window | Reconcile touches only delta tuples. Limitation: an identical tuple added by someone else is indistinguishable from ours. |
| Rule already deleted or re-added | SKIPPED_ALREADY_SATISFIED. |
| Schedule creation failure | T8, 502 `SCHEDULE_FAILED`, nothing applied. On a duplicate-name error, GetSchedule and treat it as armed. |
| Apply failure after arming | Apply runs T3 (`APPLY_FAILURE`), reconciles, deletes the schedule best-effort, returns 500. Hard crash: schedule fires and revert waits out the lease. |
| Duplicate Scheduler delivery | Live lease or terminal status → NOOP. Expired lease → T4. |
| Tag removed mid-window | Revert is AccessDenied → FAILED. Known limitation. |

## 5. API CONTRACT

All routes require the bearer token. Errors use `{"error":{"code","message","change_id?","status?"}}`.

**POST /api/changes**
```json
{"sg_id":"sg-0abc","ttl_seconds":90,"ops":[{"action":"REVOKE","protocol":"tcp","from_port":80,"to_port":80,"cidr":"0.0.0.0/0"}]}
```
201:
```json
{"change_id":"01J...","status":"PENDING","sg_id":"sg-0abc","ttl_seconds":90,"created_at":1758300000,"expires_at":1758300090,"server_time":1758300003,"schedule_name":"dm-01J...","delta":[{"op_id":1,"action":"REVOKE","protocol":"tcp","from_port":80,"to_port":80,"cidr":"0.0.0.0/0","applied":null}]}
```
Limits: 1–5 ops, IPv4 CIDR only.

**POST /api/changes/{id}/confirm** (empty body). 200:
```json
{"change_id":"01J...","status":"CONFIRMED","confirmed_at":1758300040,"idempotent":false}
```

**POST /api/changes/{id}/revert** (empty body, manual revert-now). 200:
```json
{"change_id":"01J...","status":"REVERTED","revert_trigger":"MANUAL","reverted_at":1758300031,"idempotent":false,"revert_report":[{"op_id":1,"result":"REVERTED","detail":""}]}
```
`status` may also be `PARTIAL_REVERT` or `FAILED` (still HTTP 200). The UI shows a warning for those.

**GET /api/changes/{id}**. 200:
```json
{"change_id":"01J...","status":"REVERTED","sg_id":"sg-0abc","ttl_seconds":90,"created_at":1758300000,"expires_at":1758300090,"server_time":1758300095,"apply_done":true,"delta":[],"confirmed_at":null,"reverted_at":1758300093,"revert_trigger":"SCHEDULE","revert_report":[{"op_id":1,"result":"REVERTED","detail":""}],"failure_reason":null}
```
The UI counts down with `expires_at − server_time` to avoid clock skew.

| HTTP | `error.code` | When |
|---|---|---|
| 400 | `INVALID_REQUEST` | Schema violation |
| 400 | `TTL_OUT_OF_RANGE` | Outside `MIN_TTL_SECONDS`–`MAX_TTL_SECONDS` |
| 400 | `UNSUPPORTED_RULE` | Not an IPv4 CIDR source |
| 401/403 | `UNAUTHORIZED` | Missing or bad token (API Gateway's default deny is 403, so treat both the same) |
| 403 | `SG_NOT_MANAGED` | SG lacks `deadman:managed=true` or the right `deadman:stage` |
| 404 | `SG_NOT_FOUND`, `CHANGE_NOT_FOUND` | |
| 409 | `SG_BUSY` | Active change on this SG |
| 409 | `RULE_ALREADY_EXISTS`, `RULE_NOT_FOUND` | No-op op rejected at plan time |
| 409 | `NOT_APPLIED_YET` | Confirm or revert while apply is in flight |
| 409 | `WINDOW_EXPIRED` | Late confirm |
| 409 | `CHANGE_NOT_PENDING` | Includes `status` |
| 409 | `REVERT_IN_PROGRESS` | Live revert lease |
| 502 | `SCHEDULE_FAILED` | Nothing applied |
| 500 | `APPLY_FAILED_REVERTED` / `APPLY_FAILED_REVERT_INCOMPLETE` | Includes `status` |
| 500 | `INTERNAL` | |

## 6. IAM (least privilege per role)

| Role | Statements |
|---|---|
| **apply** | DDB `PutItem, UpdateItem, DeleteItem, GetItem` on the table. `ec2:DescribeSecurityGroups, DescribeSecurityGroupRules` on `"*"`. `ec2:AuthorizeSecurityGroupIngress, RevokeSecurityGroupIngress` on `security-group/*` with Condition `StringEquals {aws:ResourceTag/deadman:managed:"true", aws:ResourceTag/deadman:stage:"<stage>"}`. `scheduler:CreateSchedule, GetSchedule, DeleteSchedule` on `schedule/deadman-<stage>/*`. `iam:PassRole` on the scheduler role with `iam:PassedToService=scheduler.amazonaws.com`. |
| **confirm** | DDB `GetItem, UpdateItem, DeleteItem`. `scheduler:DeleteSchedule` on `schedule/deadman-<stage>/*`. |
| **revert** | DDB `GetItem, UpdateItem, DeleteItem`. EC2 Describe* on `"*"`. Authorize/Revoke with the same tag conditions as apply. `scheduler:DeleteSchedule` on `schedule/deadman-<stage>/*` (manual and failure paths). |
| **status** | DDB `GetItem` only. |
| **authorizer** | CloudWatch Logs only. |
| **Scheduler role** | Trust `scheduler.amazonaws.com` (with `aws:SourceAccount`). `lambda:InvokeFunction` on the revert Lambda ARN only. |

All Lambdas also get the basic execution (logging) policy. Exact conditions and ARN shapes are subject to 1b and 1d.

## 7. REPO TREE WITH OWNERSHIP

| Path | Owner |
|---|---|
| `infra/template.yaml`, `infra/samconfig.toml`, `infra/env.example.json`, `scripts/deploy_*.sh` | M1 |
| `src/authorizer/app.py`, `probes/scheduler_probe.py` (1a, 1b, 1e) | M1 |
| `docs/MASTER_SPEC.md`, `docs/architecture.md`, `docs/COST.md`, `docs/learnings/m1.md` | M1 |
| `src/common/{rules,states,ddb,models}.py`, `src/{apply,confirm,revert,status}/app.py`, `docs/openapi.yaml`, `docs/fixtures/*.json`, `docs/learnings/m2.md` | M2 |
| `web/**` (including `web/mock/api-mock.js`), `docs/learnings/m3.md` | M3 |
| `probes/sg_probe.py` (1c, 1d), `tests/**`, `demo/**` (storyboard, recording checklist), `docs/SUBMISSION.md`, `docs/learnings/m4.md` | M4 |

M1 deploys `deadman-shared`. Anyone may deploy their own `deadman-dev-mN`.

## 8. INTERFACE MOCKS

| Consumer needs | Producer | Mock |
|---|---|---|
| M1 deploys Lambdas before code exists | M2 | Stubs returning 501 in `src/*/app.py`, committed in the first 30 min |
| M2 needs table and role ARNs | M1 | `infra/env.example.json`, plus a minimal personal stack. moto is test-only. |
| M3 needs the API | M2 | `docs/fixtures/*.json` (one per status, including `revert_manual_200.json`) driving `web/mock/api-mock.js`, which simulates PENDING → REVERTED and PENDING → CONFIRMED |
| M4 needs the API for scenarios | M2 | Scenario runner takes `BASE_URL`. `MOCK=1` plays fixtures. `sg_helpers.py` uses boto3 directly against `deadman-target-<stage>`. |
| M3 needs the deployed URL | M1 | `web/config.json` with `apiBase: "/api"`. In dev, `DevCors` plus the direct URL. |

## 9. RISKS & CUT-LINE

1. Scheduler firing delay too high: longer demo TTL and video edit, or the Step Functions fallback (1a).
2. Tag conditions unsupported for an action: code-side check, a weaker guardrail (1d).
3. Tag removal or a lost schedule defeats revert: surfaced as FAILED, not fixed.
4. CloudFront and authorizer time sink: cut to the direct API URL with CORS.
5. Apply/revert race: covered by the lease. M4 must run the scenario.
6. Shared-account collisions: the stage tag and stage rules in §2 prevent them.

**Cut order (first cut first):**
1. UI extras beyond the six §0 elements
2. Multi-op deltas (limit to 1 op)
3. T4 takeover logic
4. `SGLOCK` (replace with a racy query check)
5. CloudFront (use the API URL with CORS)
6. Late-confirm strictness

**Never cut:** T2 and T3 conditions, delta-only revert, the tag guardrail, manual revert, the video.

## 10. DECISIONS FROZEN & HOUR-0 ACTIONS

**Frozen:**
- One shared AWS account.
- Single bearer token, no Cognito.
- IPv4 CIDR only.
- Manual revert-now is in scope.
- Demo TTL comes from check 1a.
- Ship It track.

**Actions at hour 0:**
- M1 confirms all four members have console/CLI access to the shared account, whether the account is credit-funded, and sets a budget alert.
- M1 creates the repo skeleton and commits the §7 tree with stubs.
- M4 fills in the M4 name above and runs the 1c, 1d, 1f checks.

**Change control:** after freeze, only M1 edits §2–§6, as v1.1 with a delta note. Teammates re-paste only when M1 announces a new version.
