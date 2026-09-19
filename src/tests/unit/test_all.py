import os
import json
import pytest
from moto import mock_aws
import boto3
import time

# Set env vars
os.environ["AWS_DEFAULT_REGION"] = "ap-south-1"
os.environ["AWS_REGION"] = "ap-south-1"
os.environ["TABLE_NAME"] = "test-table"
os.environ["SCHEDULE_GROUP"] = "test-group"
os.environ["REVERT_FN_ARN"] = "arn:aws:lambda:ap-south-1:123456789012:function:revert"
os.environ["SCHEDULER_ROLE_ARN"] = "arn:aws:iam::123456789012:role/sched"
os.environ["STAGE"] = "m2"
os.environ["MIN_TTL_SECONDS"] = "60"
os.environ["MAX_TTL_SECONDS"] = "600"
os.environ["APPLY_LEASE_SECONDS"] = "20"
os.environ["REVERT_LEASE_SECONDS"] = "60"

from apply.app import handler as apply_handler
from confirm.app import handler as confirm_handler
from revert.app import handler as revert_handler
from status.app import handler as status_handler

@pytest.fixture
def aws_setup():
    with mock_aws():
        # setup dynamodb
        ddb = boto3.client("dynamodb")
        ddb.create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST"
        )
        # setup ec2
        ec2 = boto3.client("ec2")
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        sg = ec2.create_security_group(GroupName="test-sg", Description="test", VpcId=vpc["Vpc"]["VpcId"])
        ec2.create_tags(Resources=[sg["GroupId"]], Tags=[{"Key": "deadman:managed", "Value": "true"}, {"Key": "deadman:stage", "Value": "m2"}])
        
        # setup scheduler (moto doesn't support scheduler fully, but let's see if it works or mock it)
        # We will mock get_scheduler_client in aws.py
        yield {"sg_id": sg["GroupId"]}

def test_create_success(aws_setup, mocker):
    mocker.patch("apply.app.get_scheduler_client")
    sg_id = aws_setup["sg_id"]
    event = {
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }
    res = apply_handler(event, {})
    assert res["statusCode"] == 201
    b = json.loads(res["body"])
    assert b["status"] == "PENDING"
    assert b["delta"][0]["applied"] is True

def test_errors(aws_setup, mocker):
    mocker.patch("apply.app.get_scheduler_client")
    # 400 missing fields
    res = apply_handler({"body": "{}"}, {})
    assert res["statusCode"] == 400
    assert json.loads(res["body"])["error"]["code"] == "INVALID_REQUEST"
    
    # 400 TTL OUT OF RANGE
    event = {"body": json.dumps({"sg_id": "sg-123", "ttl_seconds": 10, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}
    res = apply_handler(event, {})
    assert res["statusCode"] == 400
    assert json.loads(res["body"])["error"]["code"] == "TTL_OUT_OF_RANGE"

def test_late_confirm(aws_setup, mocker):
    mocker.patch("apply.app.get_scheduler_client")
    sg_id = aws_setup["sg_id"]
    res = apply_handler({"body": json.dumps({"sg_id": sg_id, "ttl_seconds": 90, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}, {})
    cid = json.loads(res["body"])["change_id"]
    
    # modify expires_at to be in the past
    table = boto3.resource("dynamodb").Table("test-table")
    table.update_item(Key={"pk": f"CHG#{cid}"}, UpdateExpression="SET expires_at = :past", ExpressionAttributeValues={":past": int(time.time()) - 100})
    
    res2 = confirm_handler({"pathParameters": {"id": cid}}, {})
    assert res2["statusCode"] == 409
    assert json.loads(res2["body"])["error"]["code"] == "WINDOW_EXPIRED"

def test_double_confirm(aws_setup, mocker):
    mocker.patch("apply.app.get_scheduler_client")
    mocker.patch("confirm.app.get_scheduler_client")
    sg_id = aws_setup["sg_id"]
    res = apply_handler({"body": json.dumps({"sg_id": sg_id, "ttl_seconds": 90, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}, {})
    cid = json.loads(res["body"])["change_id"]
    
    res2 = confirm_handler({"pathParameters": {"id": cid}}, {})
    assert res2["statusCode"] == 200
    
    res3 = confirm_handler({"pathParameters": {"id": cid}}, {})
    assert res3["statusCode"] == 200
    assert json.loads(res3["body"])["idempotent"] is True

def test_manual_revert(aws_setup, mocker):
    mocker.patch("apply.app.get_scheduler_client")
    mocker.patch("revert.app.get_scheduler_client")
    sg_id = aws_setup["sg_id"]
    res = apply_handler({"body": json.dumps({"sg_id": sg_id, "ttl_seconds": 90, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}, {})
    cid = json.loads(res["body"])["change_id"]
    
    class FakeContext:
        aws_request_id = "test-req"
    
    event = {"requestContext": {"http": {}}, "pathParameters": {"id": cid}}
    res2 = revert_handler(event, FakeContext())
    print("REVERT RES2:", res2)
    assert res2["statusCode"] == 200
    assert json.loads(res2["body"])["status"] == "REVERTED"

