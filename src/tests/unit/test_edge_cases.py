import os
import json
import pytest
from moto import mock_aws
import boto3
import time

from apply.app import handler as apply_handler
from confirm.app import handler as confirm_handler
from revert.app import handler as revert_handler

class FakeContext:
    aws_request_id = "test-req"

@pytest.fixture
def aws_setup():
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ap-south-1")
        ddb.create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST"
        )
        ec2 = boto3.client("ec2", region_name="ap-south-1")
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
        sg = ec2.create_security_group(GroupName="test-sg", Description="test", VpcId=vpc["Vpc"]["VpcId"])
        ec2.create_tags(Resources=[sg["GroupId"]], Tags=[{"Key": "deadman:managed", "Value": "true"}, {"Key": "deadman:stage", "Value": "m2"}])
        yield {"sg_id": sg["GroupId"]}

def test_late_confirm(aws_setup, mocker):
    mocker.patch("apply.app.get_scheduler_client")
    sg_id = aws_setup["sg_id"]
    res = apply_handler({"body": json.dumps({"sg_id": sg_id, "ttl_seconds": 90, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}, {})
    cid = json.loads(res["body"])["change_id"]
    table = boto3.resource("dynamodb", region_name="ap-south-1").Table("test-table")
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

def test_manual_vs_scheduled_revert(aws_setup, mocker):
    mocker.patch("apply.app.get_scheduler_client")
    mocker.patch("revert.app.get_scheduler_client")
    sg_id = aws_setup["sg_id"]
    res = apply_handler({"body": json.dumps({"sg_id": sg_id, "ttl_seconds": 90, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}, {})
    cid = json.loads(res["body"])["change_id"]
    
    event_manual = {"requestContext": {"http": {}}, "pathParameters": {"id": cid}}
    res_m = revert_handler(event_manual, FakeContext())
    assert res_m["statusCode"] == 200
    assert json.loads(res_m["body"])["status"] == "REVERTED"
    
    # manual revert again should return idempotent
    res_m2 = revert_handler(event_manual, FakeContext())
    print("RES M2:", res_m2)
    assert res_m2["statusCode"] == 200
    assert json.loads(res_m2["body"])["idempotent"] is True
    
    # scheduled revert should return None (NOOP)
    event_sched = {"trigger": "SCHEDULE", "change_id": cid}
    res_s = revert_handler(event_sched, FakeContext())
    assert res_s is None
