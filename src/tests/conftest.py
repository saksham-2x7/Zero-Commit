"""
Pytest configuration and AWS mocks for Deadman unit tests.
"""
import os
import sys
from pathlib import Path
import pytest
import boto3
from moto import mock_aws

# Ensure src is in sys.path
src_path = str(Path(__file__).resolve().parent.parent)
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from common.aws import set_ec2_client, set_dynamodb_client, set_scheduler_client, reset_clients


class MockSchedulerClient:
    def __init__(self):
        self.schedules = {}
        self.should_fail_create = False
        self.create_error_code = None
        self.create_exception = None

    def create_schedule(self, Name, GroupName, ScheduleExpression, FlexibleTimeWindow, Target, ActionAfterCompletion="DELETE"):
        if self.create_exception is not None:
            raise self.create_exception
        if self.should_fail_create:
            from botocore.exceptions import ClientError
            raise ClientError(
                {"Error": {"Code": self.create_error_code or "InternalServerException", "Message": "Mock schedule error"}},
                "CreateSchedule"
            )
        if Name in self.schedules:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "ConflictException", "Message": "Schedule already exists"}}, "CreateSchedule")
        self.schedules[Name] = {
            "GroupName": GroupName,
            "ScheduleExpression": ScheduleExpression,
            "Target": Target,
            "ActionAfterCompletion": ActionAfterCompletion,
        }
        return {"ScheduleArn": f"arn:aws:scheduler:ap-south-1:123456789012:schedule/{GroupName}/{Name}"}

    def delete_schedule(self, Name, GroupName):
        if Name in self.schedules:
            del self.schedules[Name]
            return {}
        from botocore.exceptions import ClientError
        raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "Schedule not found"}}, "DeleteSchedule")

    def get_schedule(self, Name, GroupName):
        if Name in self.schedules:
            return self.schedules[Name]
        from botocore.exceptions import ClientError
        raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "Schedule not found"}}, "GetSchedule")


class MockContext:
    def __init__(self, request_id="req-test-12345"):
        self.aws_request_id = request_id


@pytest.fixture(autouse=True)
def setup_env():
    os.environ["AWS_DEFAULT_REGION"] = "ap-south-1"
    os.environ["AWS_REGION"] = "ap-south-1"
    os.environ["TABLE_NAME"] = "deadman-changes-shared"
    os.environ["SCHEDULE_GROUP"] = "deadman-shared"
    os.environ["REVERT_FN_ARN"] = "arn:aws:lambda:ap-south-1:123456789012:function:deadman-revert-shared"
    os.environ["SCHEDULER_ROLE_ARN"] = "arn:aws:iam::123456789012:role/deadman-scheduler-shared"
    os.environ["MANAGED_TAG_KEY"] = "deadman:managed"
    os.environ["MANAGED_TAG_VALUE"] = "true"
    os.environ["STAGE_TAG_KEY"] = "deadman:stage"
    os.environ["STAGE"] = "shared"
    os.environ["MIN_TTL_SECONDS"] = "60"
    os.environ["MAX_TTL_SECONDS"] = "600"
    os.environ["APPLY_LEASE_SECONDS"] = "20"
    os.environ["REVERT_LEASE_SECONDS"] = "60"
    yield
    reset_clients()


@pytest.fixture
def aws_env(setup_env):
    with mock_aws():
        # Setup DynamoDB
        ddb = boto3.client("dynamodb", region_name="ap-south-1")
        ddb.create_table(
            TableName="deadman-changes-shared",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        set_dynamodb_client(ddb)

        # Setup EC2
        ec2 = boto3.client("ec2", region_name="ap-south-1")
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        vpc_id = vpc["Vpc"]["VpcId"]

        sg = ec2.create_security_group(
            GroupName="deadman-target-shared",
            Description="Test target security group",
            VpcId=vpc_id,
        )
        sg_id = sg["GroupId"]

        # Tag SG
        ec2.create_tags(
            Resources=[sg_id],
            Tags=[
                {"Key": "deadman:managed", "Value": "true"},
                {"Key": "deadman:stage", "Value": "shared"},
            ],
        )
        set_ec2_client(ec2)

        # Setup Scheduler
        scheduler = MockSchedulerClient()
        set_scheduler_client(scheduler)

        yield {
            "ddb": ddb,
            "ec2": ec2,
            "scheduler": scheduler,
            "sg_id": sg_id,
            "vpc_id": vpc_id,
        }
