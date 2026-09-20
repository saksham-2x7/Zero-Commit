# Verification M4 (checks 1c, 1d, 1f)

Source of criteria: `docs/MASTER_SPEC.md` §1. Region `ap-south-1`.

**Rule:** a check is `NOT RUN` until it has run on real AWS. Dry runs against moto (`probes/sg_probe.py --moto`)
only debug the script and are never recorded here.

| Check | Assumptions | Status |
|---|---|---|
| 1c | A-9, A-10, A-11 | NOT RUN |
| 1d | A-13, A-14 | NOT RUN |
| 1f | none | PARTIAL: general web searches only (by Claude in chat), Devpost NOT CHECKED (not PASS) |
| S1-S10 scenario suite (`tests/scenarios/run.py`) | none | NOT RUN against the live stack |
| Live manual walkthrough (below) | none | observed by M4, not scripted |

Run: `python probes/sg_probe.py --yes-aws --write-md` (overwrites the marked result blocks below).

## Live results (M4, Sat 19 Sep 2026, region ap-southeast-2, through the deployed website)

Manual walkthrough by M4 in the browser on the deployed CloudFront site, recorded as reported. These are observations, not
output from `tests/scenarios/run.py` and not the probe. The spec and probe default to `ap-south-1`; this walkthrough
ran in `ap-southeast-2`, so nothing here is evidence for the `ap-south-1` assumptions.

| Flow | Observed |
|---|---|
| Apply | Cutting tcp/80 took effect: the demo server went dark. |
| Timeout auto-revert | Worked. Timeline: PENDING 01:54:07 PM, REVERTED (SCHEDULE) 01:55:41 PM, on a 60 s TTL: about 94 s total. |
| Confirm | Worked. AUTHORIZE tcp 8080 from 10.0.0.0/8 reached status CONFIRMED. |
| Manual revert | Worked. REVERTED (MANUAL), 5 s after Apply. |
| Wrong token | Refused. |

Note on timing: 94 s total on a 60 s TTL means the revert landed roughly 34 s after the window closed, if the PENDING
timestamp is the arming time. Spec check 1a (M1's, not evaluated here) bounds the firing delay at -1..15 s. Not recorded as
a 1a result, but worth M1 looking at before the video.

Still **NOT RUN**: 1c and 1d (probe never run on real AWS), the S1-S10 suite against the live stack. 1f stays **PARTIAL**
(web searches only, Devpost not checked).

## 1c: rule ids, revoke with different Description, describe latency

Pass criteria (spec): A ≠ B (A-9); (i) revoke with a different Description removes the rule (A-10);
(ii) rule visible in describe within 5 s in 20/20 trials (A-11).
Fallbacks: (i) fails → revoke by rule id (needs the security-group-rule ARN in IAM, A-12); (ii) fails → poll up to 10 s with backoff.

<!-- 1c:begin -->
**Status: NOT RUN**
<!-- 1c:end -->

## 1d: tag conditions per action

Pass criteria (spec): filled matrix (action → resource-level? tag enforced?); Authorize/Revoke on an SG with a
missing or wrong tag returns AccessDenied; Describe* expected to need `"*"` (A-14).
Fallback: Resource `"*"` plus a mandatory in-code tag check before every mutation and at revert (weaker guardrail).

<!-- 1d:begin -->
**Status: NOT RUN**
<!-- 1d:end -->

## 1f: prior-art searches (web searches by Claude in chat; Devpost still to be done by hand)

Pass criteria (spec): no project does confirm-or-auto-revert on AWS resources. Fallback: reframe the pitch around the closest hit's gap.

**Source: web searches run by Claude in chat (GitHub and general web only; none reached Devpost). Not a manual search by M4, and not independently re-verified.**
"No exact clone found on GitHub/web search. Closest: tf_aws_lambda_ip_whitelist, marekq/robothor aws-lambda-firewall, bushong1/lambda-revoke-sg, palmetto aws-security-group-add-ip-action (all expiring grants, polled cleanup), Securosis 2016 auto-revert of any SG change (no human confirm). Devpost NOT CHECKED, to be searched manually."

Verdict: **PARTIAL, not PASS.** 1f becomes PASS only after M4 says the manual Devpost/GitHub searches for all five queries have been done.

**How the searches were run:** general web searches by Claude in chat, with strings reworded from the spec's 1f queries (spec text is in the "Spec query" column). None was scoped to Devpost, and no Devpost pages were returned. Even query 1, which starts with the word "Devpost", was a general web search.

| # | Spec query | Exact string run (general web search) | Manual search by M4 still needed |
|---|---|---|---|
| 1 | Devpost `"commit confirmed" AWS security group` | `Devpost "commit confirmed" AWS security group` | YES: Devpost and GitHub |
| 2 | GitHub `"dead man's switch" security group EventBridge Scheduler revert` | `"dead man's switch" security group EventBridge Scheduler revert AWS` | YES: Devpost and GitHub |
| 3 | Devpost `auto-revert "security group" hackathon AWS` | `auto-revert security group change hackathon AWS lambda rollback timer confirm` | YES: Devpost and GitHub |
| 4 | GitHub `"one-time schedule" revoke_security_group_ingress rollback` | `"one-time schedule" revoke_security_group_ingress rollback lambda scheduler temporary security group rule` | YES: Devpost and GitHub |
| 5 | Google/Product Hunt `confirm or rollback AWS infrastructure change "EventBridge Scheduler"` | `confirm or rollback AWS infrastructure change "EventBridge Scheduler" auto rollback unless confirmed` | YES: Devpost and GitHub |

All five spec queries, including the two Devpost ones (1 and 3), are still to be run manually by M4 on Devpost and GitHub. Results in the quoted summary above come from the general web searches only.
