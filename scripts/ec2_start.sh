#!/bin/bash
STAGE=${1:-shared}
INSTANCE_ID=$(aws ec2 describe-instances --filters "Name=tag:deadman:stage,Values=${STAGE}" "Name=instance-state-name,Values=stopped" --query "Reservations[0].Instances[0].InstanceId" --output text)
aws ec2 start-instances --instance-ids ${INSTANCE_ID}
