import os
import json
import pytest
from moto import mock_aws
import boto3
import time

from apply.app import handler as apply_handler
from confirm.app import handler as confirm_handler
from revert.app import handler as revert_handler
from status.app import handler as status_handler

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

def test_errors(aws_setup, mocker):
    mocker.patch("apply.app.get_scheduler_client")
    sg_id = aws_setup["sg_id"]
    
    # 400 INVALID_REQUEST
    res = apply_handler({"body": "{}"}, {})
    assert res["statusCode"] == 400
    assert json.loads(res["body"])["error"]["code"] == "INVALID_REQUEST"
    
    # 400 TTL_OUT_OF_RANGE
    res = apply_handler({"body": json.dumps({"sg_id": sg_id, "ttl_seconds": 10, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}, {})
    assert res["statusCode"] == 400
    assert json.loads(res["body"])["error"]["code"] == "TTL_OUT_OF_RANGE"
    
    # 400 UNSUPPORTED_RULE
    res = apply_handler({"body": json.dumps({"sg_id": sg_id, "ttl_seconds": 90, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "ipv6": "::/0"}]})}, {})
    assert res["statusCode"] == 400
    assert json.loads(res["body"])["error"]["code"] == "UNSUPPORTED_RULE"
    
    # 403 SG_NOT_MANAGED
    ec2 = boto3.client("ec2", region_name="ap-south-1")
    sg2 = ec2.create_security_group(GroupName="test-sg2", Description="test")
    res = apply_handler({"body": json.dumps({"sg_id": sg2["GroupId"], "ttl_seconds": 90, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}, {})
    assert res["statusCode"] == 403
    assert json.loads(res["body"])["error"]["code"] == "SG_NOT_MANAGED"
    
    # 404 SG_NOT_FOUND
    res = apply_handler({"body": json.dumps({"sg_id": "sg-99999999", "ttl_seconds": 90, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}, {})
    assert res["statusCode"] == 404
    assert json.loads(res["body"])["error"]["code"] == "SG_NOT_FOUND"
    
    # 404 CHANGE_NOT_FOUND
    res = status_handler({"pathParameters": {"id": "01Jxxx"}}, {})
    assert res["statusCode"] == 404
    assert json.loads(res["body"])["error"]["code"] == "CHANGE_NOT_FOUND"
    
    # 409 SG_BUSY
    res1 = apply_handler({"body": json.dumps({"sg_id": sg_id, "ttl_seconds": 90, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}, {})
    res2 = apply_handler({"body": json.dumps({"sg_id": sg_id, "ttl_seconds": 90, "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "1.1.1.1/32"}]})}, {})
    assert res2["statusCode"] == 409
    assert json.loads(res2["body"])["error"]["code"] == "SG_BUSY"
    
    # 409 RULE_ALREADY_EXISTS / RULE_NOT_FOUND
    ec2.create_tags(Resources=[sg2["GroupId"]], Tags=[{"Key": "deadman:managed", "Value": "true"}, {"Key": "deadman:stage", "Value": "m2"}])
    res = apply_handler({"body": json.dumps({"sg_id": sg2["GroupId"], "ttl_seconds": 90, "ops": [{"action": "REVOKE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]})}, {})
    assert res["statusCode"] == 409
    assert json.loads(res["body"])["error"]["code"] == "RULE_NOT_FOUND"
    
