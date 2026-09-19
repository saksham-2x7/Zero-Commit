# Storyboard: 3:00 video (spec section 0)

Recorded (no live demo) from the deployed CloudFront URL. At least 2 full takes. 1080p.
Two windows side by side: browser (Deadman UI) left, terminal running `python demo/curl_loop.py <target-ip>` right.
`<TTL>` = demo TTL, the smallest passing T from check 1a (`docs/verification_m1.md`). Voice-over lines are drafts.

**How Deadman differs (use in the 0:00-0:25 beat):** Existing tools add or expire access and poll for cleanup: they grant an IP for a while, then a scheduled sweep removes it. Deadman snapshots any security-group change and reverts it on a precise one-time timer unless a human confirms. Closest prior art found so far: tf_aws_lambda_ip_whitelist, aws-lambda-firewall (marekq/robothor), lambda-revoke-sg (bushong1), aws-security-group-add-ip-action (palmetto), and a 2016 Securosis auto-revert of any SG change with no human confirm. Devpost has not been searched yet, so this claim is provisional (see `docs/verification_m4.md`, 1f).

| Time | Screen | Voice-over (draft) |
|---|---|---|
| **0:00-0:25** Problem, who, the other side | Title card, then terminal: curl loop green. Cut to a console view of the security group with the public HTTP rule. | "Deadman is a safety net for security-group changes: apply a change, and it undoes itself on a timer unless you confirm it. If you have ever tightened a firewall rule remotely and locked yourself out, this is for you: on-call engineers, small teams, students. On the other side of that mistake are the users of the service that goes dark." |
| **0:25-1:10** Confirm path | UI: paste token, target SG, form REVOKE tcp/80 0.0.0.0/0, TTL `<TTL>`. Planned delta shown, submit. Ring starts, loop turns red (rule revoked). Click **CONFIRM** before zero. Timeline: PENDING -> CONFIRMED, ring stops. | "I remove the public HTTP rule. The change is live and a countdown is running. This one is intended, so I confirm. The timer is cancelled and the change stays." |
| **1:10-2:10** Timeout path | Restore the rule (off camera). Apply the same change again. Loop red, ring drains, hands off the keyboard. At zero the loop turns green; timeline REVERTING -> REVERTED, trigger SCHEDULE. Cut to AWS console: Scheduler group `deadman-shared`, schedule `dm-<id>` is gone. Cut to the DynamoDB item: status, delta, revert_report. 5 s cut-in: apply again, click **REVERT NOW**, loop green, trigger MANUAL. | "Now I make the mistake and do nothing. Traffic dies. When the timer runs out Deadman puts the rule back by itself; nobody logs in. The one-time schedule deleted itself, and the DynamoDB item shows exactly which operation was undone. Spot it early and REVERT NOW does the same thing immediately." |
| **2:10-2:45** Where AWS fits, cost, learned | Rendered diagram from `docs/architecture.md`. Overlay: Lambda, API Gateway, DynamoDB, EventBridge Scheduler, S3 + CloudFront, IAM. Cost slide from `docs/COST.md` (no dollar figures). One learning line per member from `docs/learnings/m*.md`. | "Everything is pay-per-use except the test instance, which we stop between sessions. The revert only touches the rules in the recorded change, and IAM conditions limit it to tagged groups. We learned how one-time schedules, PassRole and conditional writes fit together." |
| **2:45-3:00** Close | UI final frame, CloudFront URL, repo URL. | "Deadman: change firewall rules without fear. The link is below." |

## Rules for the cut
- Show only what is verified on the shared stack. If check 1a forced a longer TTL, cut the wait in editing with a visible marker; never fake the revert.
- Do not show the bearer token or the account ID (blur the console header and the token field).
- Curl loop font >= 20 pt so it reads at 1080p.
