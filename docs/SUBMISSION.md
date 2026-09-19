# Deadman: submission (skeleton, M4)

TODO sections are filled only from verified results. Nothing here claims behaviour that has not run on the shared stack.

**Links**
- Deployed URL (CloudFront): TODO (from M1's stack outputs)
- Repo: https://github.com/saksham-2x7/Zero-Commit
- Video (3:00): TODO (after recording)

## What it does
Deadman is a safety net for security-group changes: apply a change, and it undoes itself on a timer unless you confirm it. TODO: 3-4 sentences on the flow: apply, countdown, then CONFIRM, REVERT NOW, or auto-revert; only the changed rules are reverted.

## Who it is for, and the other side
Anyone changing firewall rules remotely: on-call engineers, small teams, students. On the other side are the users of the service that goes dark when a bad rule locks the operator out. TODO: one concrete example from the demo.

## Where AWS fits
| Service | Role |
|---|---|
| API Gateway (HTTP API) + Lambda authorizer | Four routes under `/api`, bearer-token check |
| Lambda (apply, confirm, revert, status) | State machine and security-group changes |
| DynamoDB | Change records, per-SG lock, conditional writes as the linearisation point |
| EventBridge Scheduler | One-time schedule per change that triggers the revert |
| S3 + CloudFront | Static UI and `/api/*` on one domain |
| IAM | Least privilege; tag conditions and PassRole scoping. Actual behaviour per `docs/verification_m1.md` and `docs/verification_m4.md` (1d) |

## Cost decisions
TODO: summarise `docs/COST.md`. No dollar figures unless cited from AWS pricing pages.

## Verification status
TODO: PASS/FAIL per check from `docs/verification_m1.md`, `docs/verification_m2.md`, `docs/verification_m4.md`, plus the S1-S10 table from the shared stack with timings. Failures and INCONCLUSIVE results listed as such.

## What we learned
TODO: one paragraph each from `docs/learnings/m1.md` to `m4.md`.

## Known limitations
From the spec: one shared bearer token; IPv4 CIDR only; a tag removed mid-window makes revert AccessDenied (FAILED); an identical rule tuple added by someone else in the window is indistinguishable from ours.
