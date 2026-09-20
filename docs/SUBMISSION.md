# Deadman: submission draft

- **Live URL:** https://d4uzp0bsghs2h.cloudfront.net (sign-in needs the team's bearer token, which is not published here)
- **Repo:** https://github.com/saksham-2x7/Zero-Commit
- **Video (3:00):** TODO

## What it does
Deadman is a safety net for security-group changes: apply a change, and it undoes itself on a timer unless you confirm it. You choose a security group, a time window and up to five rules to add or remove. Deadman snapshots the group, applies the change and starts a countdown. CONFIRM makes the change permanent; REVERT NOW undoes it immediately; if nobody acts, the change reverts on its own when the timer runs out. Only the rules in the recorded change are touched, so an unrelated edit made in the meantime survives the revert.

## Who it is for, and what changes for the people on the other side
It is for anyone changing firewall rules remotely: on-call engineers, small teams, students. The people on the other side are the users of the service behind that firewall. Today a bad rule can lock the operator out and leave the service dark until someone gets console access. With Deadman the outage is bounded by the timer: in our live run, cutting port 80 took the demo server offline, and it came back by itself without anyone logging in.

## How Deadman differs
Existing tools we found add or expire access and poll for cleanup. Deadman snapshots any change and reverts it on a one-time timer unless a human confirms. Our prior-art search was general web searches only; Devpost has not been searched, so this comparison is provisional (`docs/verification_m4.md`, 1f).

## Where AWS fits
| Service | Role |
|---|---|
| S3 + CloudFront | Static web UI, and `/api/*` on the same domain (no CORS) |
| API Gateway (HTTP API) + Lambda authorizer | Four routes, bearer-token check |
| Lambda | Apply, confirm, revert and status logic |
| DynamoDB | Change records, per-group lock, conditional writes as the single source of truth for state |
| EventBridge Scheduler | One-time schedule per change that fires the revert |
| EC2 | Small demo server whose port 80 we cut and restore |

## Cost decisions
Everything except the demo server is pay-per-use and scales to zero when idle. The demo server is the only always-on piece; we stop it between sessions. We give no dollar figures; see `docs/COST.md`.

## What we learned
- Mocks and unit tests passed, but real AWS rejected two things they missed: an environment-variable name mismatch, and a DynamoDB expression bug.
- EventBridge Scheduler needs the exact function ARN as its target.
- One-time schedules, PassRole and conditional writes are easy to get subtly wrong; running against real AWS early caught what tests could not.

## Verification status
Observed by hand on the deployed site (region ap-southeast-2): apply cuts port 80 and the server goes dark; timeout auto-revert, confirm, manual revert and wrong-token refusal all worked (details in `docs/verification_m4.md`). Not done: security-group probes 1c and 1d, the S1-S10 scenario suite against the live stack, and the Devpost half of the prior-art check.

## Limitations
This is a hackathon prototype. One shared bearer token protects it, with no per-user identity. It handles IPv4 ingress rules only. Revert can only undo what it recorded: if someone removes the group's Deadman tags mid-window, the revert fails and reports it. An identical rule added by someone else during the window cannot be told apart from ours. In our one timed run the auto-revert landed about 34 s after the 60 s window closed, which is later than we would like and has not been investigated. Results come from a single manual walkthrough, not a repeated or scripted test run, and the demo server is not designed to be left running.
