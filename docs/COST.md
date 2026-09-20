# Cost Analysis

This project is designed to be highly cost-efficient by leaning heavily into serverless and pay-per-use managed services.

### Scale-to-Zero Services
The following services charge solely based on usage (requests, compute time, or storage) and scale to zero when no changes are being processed:
* **AWS Lambda**: Billed per invocation and compute duration.
* **Amazon API Gateway**: Billed per API request.
* **Amazon DynamoDB**: Configured for On-Demand capacity, billed per read/write request and storage volume.
* **Amazon S3 & CloudFront**: Billed for storage and outbound data transfer.
* **Amazon EventBridge Scheduler**: Billed per schedule execution.

### Fixed Costs
* **Amazon EC2**: The demonstration server is the only always-on piece of infrastructure. To minimize costs, this instance can be stopped between demonstration sessions.
