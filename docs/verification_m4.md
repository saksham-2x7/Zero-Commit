# Verification M4 (checks 1c, 1d, 1f)

Source of criteria: `docs/MASTER_SPEC.md` §1. Region `ap-south-1`.

**Rule:** a check is `NOT RUN` until it has run on real AWS. Dry runs against moto (`probes/sg_probe.py --moto`)
only debug the script and are never recorded here.

| Check | Assumptions | Status |
|---|---|---|
| 1c | A-9, A-10, A-11 | NOT RUN |
| 1d | A-13, A-14 | NOT RUN |
| 1f | none | PARTIAL: web/GitHub done, Devpost NOT CHECKED (not PASS) |

Run: `python probes/sg_probe.py --yes-aws --write-md` (overwrites the marked result blocks below).

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

## 1f: prior-art searches (run by hand by M4)

Pass criteria (spec): no project does confirm-or-auto-revert on AWS resources. Fallback: reframe the pitch around the closest hit's gap.

**Result as reported by M4 (manual search, not re-verified by the agent):**
"No exact clone found on GitHub/web search. Closest: tf_aws_lambda_ip_whitelist, marekq/robothor aws-lambda-firewall, bushong1/lambda-revoke-sg, palmetto aws-security-group-add-ip-action (all expiring grants, polled cleanup), Securosis 2016 auto-revert of any SG change (no human confirm). Devpost NOT CHECKED, to be searched manually."

Verdict: **PARTIAL, not PASS.** 1f becomes PASS only after M4 confirms the Devpost searches (queries 1 and 3) were run manually.

| # | Query | Status |
|---|---|---|
| 1 | Devpost `"commit confirmed" AWS security group` | NOT CHECKED |
| 2 | GitHub `"dead man's switch" security group EventBridge Scheduler revert` | searched, no exact clone |
| 3 | Devpost `auto-revert "security group" hackathon AWS` | NOT CHECKED |
| 4 | GitHub `"one-time schedule" revoke_security_group_ingress rollback` | searched, no exact clone |
| 5 | Google/Product Hunt `confirm or rollback AWS infrastructure change "EventBridge Scheduler"` | searched, no exact clone |

Per-query status is inferred from the summary above; confirm or correct it when Devpost is done.
