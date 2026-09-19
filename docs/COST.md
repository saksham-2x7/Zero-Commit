# Cost Analysis

## Services that Scale to Zero (Pay-per-use)
- **AWS Lambda**: Billed per invocation and duration. Scales to zero when no transactions are active.
- **Amazon API Gateway (HTTP API)**: Billed per million requests. Scales to zero.
- **Amazon DynamoDB (On-Demand)**: Billed per read/write request. Scales to zero (storage costs still apply for snapshots, but TTL purges them).
- **Amazon EventBridge Scheduler**: Billed per million invocations. Scales to zero.
- **Amazon CloudFront**: Billed per GB transferred. Scales to zero.
- **Amazon S3**: Billed per GB stored and requests. Almost zero for a small frontend.

## Always-On Services (Fixed Costs)
- **Amazon EC2 (Target Instance)**: An EC2 instance (e.g., `t3.nano`) runs continuously in the shared/m4 stages to test the application. This incurs a fixed hourly charge.
- **Data Transfer**: Any outbound data transfer from the AWS network.

Overall, the core "Deadman" transaction engine is fully serverless and scales to zero, costing essentially nothing when idle.
