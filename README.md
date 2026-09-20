# Deadman

A safety net for AWS security-group changes. You apply a change, and it undoes itself on a timer unless you confirm it ("commit confirmed").

**Who it's for:** Anyone changing firewall rules under pressure, and the users on the other side of an outage.

## Live Demo

**URL:** [https://d4uzp0bsghs2h.cloudfront.net](https://d4uzp0bsghs2h.cloudfront.net)

*(A bearer token is required; it is provided separately in the submission, never in the repo).*

## How It Works (4 Steps)

1. **Arm first:** Check the group is tagged `deadman:managed=true`, save a snapshot and plan in DynamoDB, and create a one-time EventBridge Scheduler timer — all before touching the firewall.
2. **Cut:** Apply the change to the security group.
3. **Countdown:** The safety window ticks down while the operator verifies connectivity and service health.
4. **Confirm or Revert:** Confirm makes the change permanent (deleting the schedule and releasing the lock). Otherwise, the revert Lambda undoes only Deadman's own changes upon timer expiration (or immediately if you click Revert Now).

## Architecture

Region: `ap-southeast-2`

| Component | Technology | Responsibility |
|---|---|---|
| **Frontend & CDN** | S3 + CloudFront | Serves static React UI and reverse-proxies `/api/*` requests to API Gateway |
| **API & Auth** | API Gateway + Lambda Authorizer | REST API interface; validates Bearer token on all mutating/query endpoints |
| **Core Functions** | 4 Python Lambdas | `apply` (arms & cuts), `confirm` (commits), `revert` (rollbacks), `status` (polls change & server time) |
| **State & Lock Storage** | DynamoDB | Single-table design for change records, rule snapshots, and per-security-group locks (`SGLOCK#<sg_id>`) |
| **Deadman Timer** | EventBridge Scheduler | One-time schedule configured with `ActionAfterCompletion='DELETE'` targeting the `revert` Lambda |
| **Target Workload** | EC2 + Security Group | Demo server and managed security group tagged `deadman:managed=true` |

## Verified on the Live Stack

- Apply removed the port-80 rule and the demo server stopped answering.
- The timer ended and the status changed to REVERTED (SCHEDULE) with the server answering again.
- Confirm showed CONFIRMED and the rule stayed.
- Revert Now showed REVERTED (MANUAL) within about 5 seconds.
- A wrong or missing token was refused.
- 64 unit tests pass (mocks).

## Known Limitations

> "The revert lands after the timer ends (about 30 seconds late in one measured run; not characterised). IAM tag-guardrail negative tests and the full scenario suite were not run on the live stack; the code also checks tags itself as a backup. One shared token, IPv4 rules only, one active change per security group, single region (ap-southeast-2). Similar-project search was limited to GitHub and general web."

Cause of the late revert: Scheduler invoked the revert function about 28 seconds after the requested time in the one run we inspected; the revert function itself took about 6 seconds.

## How to Run Locally

### Frontend Mock UI
```bash
cd web
npm ci
VITE_MOCK=1 npm run dev
```

### Unit Tests
```bash
python -m pytest src/tests -q
```

## Team

- **Saksham** (infra)
- **Hamza** (core)
- **Janani** (frontend)
- **Tanvi** (tests/demo/docs)
