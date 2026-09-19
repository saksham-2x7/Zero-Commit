# Verification Results (M1)

## Check 1a (Firing Delay)
- **Trials:** 10 trials for Scheduler `at()` trigger.
- **Result:** PASS. Average delay is ~3 seconds. 
- **Chosen demo TTL:** 90 seconds
- **MIN_TTL_SECONDS:** 30 seconds

## Check 1b (IAM Permissions)
- **Result:** PASS. `iam:PassRole` with `scheduler.amazonaws.com` is required and functional. DDB permissions verified.

## Check 1e (ActionAfterCompletion)
- **Result:** PASS. Schedule auto-deletes regardless of target success/failure when set to DELETE.

## Check 1g (CloudFront Auth)
- **Result:** PENDING (Will verify in T5)

## Check 1g (CloudFront Auth)
- **Result:** PASS. Accessing the CloudFront URL with a valid Bearer token returns 200 OK. Requests without a token or with an invalid token return 401/403.
