# Verification Results (M2)

- **Unit tests**: `test_all.py`, `test_errors.py`, `test_edge_cases.py` passing via Moto (mocking DynamoDB, EC2).
- **Edge cases covered**:
  - Idempotency on double confirm (T2 idempotency)
  - Late confirm after window expiration -> WINDOW_EXPIRED
  - Conflict on SG_BUSY
  - Invalid requests (TTL limits, missing fields, unmanaged SG)
  - Precondition checks logic during revert (simulated via mocking)
- **Live AWS Deploy**: Attempted `scripts/deploy_dev.sh m2` but `sam` CLI was not available in the execution environment. Simulated full apply-confirm-revert loop via exhaustive mocked unit tests.
- **Spec check 1h**: Manual test for CloudFront revert taking under 10s is blocked on SAM deployment.
