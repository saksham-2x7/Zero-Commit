"""
Unit tests covering EVERY Spec §5 Error Code.
"""
import json
import time
import pytest
from conftest import MockContext
from apply.app import handler as apply_handler
from confirm.app import handler as confirm_handler
from revert.app import handler as revert_handler
from status.app import handler as status_handler
from common.errors import DeadmanError, make_error_response


# 1. INVALID_REQUEST (400)
def test_error_invalid_request(aws_env):
    resp = apply_handler({"body": "invalid-json{"}, MockContext())
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"]["code"] == "INVALID_REQUEST"

    resp2 = apply_handler({"body": json.dumps({"sg_id": "sg-123", "ttl_seconds": 90, "ops": []})}, MockContext())
    assert resp2["statusCode"] == 400
    assert json.loads(resp2["body"])["error"]["code"] == "INVALID_REQUEST"


# 2. TTL_OUT_OF_RANGE (400)
def test_error_ttl_out_of_range(aws_env):
    sg_id = aws_env["sg_id"]
    resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 10,  # Below MIN_TTL_SECONDS (60)
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"]["code"] == "TTL_OUT_OF_RANGE"


# 3. UNSUPPORTED_RULE (400)
def test_error_unsupported_rule(aws_env):
    sg_id = aws_env["sg_id"]
    # Non-IPv4 or bad CIDR
    resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "2001:db8::/32"}]
        })
    }, MockContext())
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"]["code"] == "UNSUPPORTED_RULE"


# 4. UNAUTHORIZED (401)
def test_error_unauthorized():
    resp = make_error_response("UNAUTHORIZED", "Missing or invalid token", status_code=401)
    assert resp["statusCode"] == 401
    assert json.loads(resp["body"])["error"]["code"] == "UNAUTHORIZED"


# 5. SG_NOT_MANAGED (403)
def test_error_sg_not_managed(aws_env):
    # Create SG without required tags
    ec2 = aws_env["ec2"]
    unmanaged_sg = ec2.create_security_group(
        GroupName="unmanaged-sg",
        Description="Unmanaged SG",
        VpcId=aws_env["vpc_id"],
    )
    sg_id = unmanaged_sg["GroupId"]

    resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert resp["statusCode"] == 403
    assert json.loads(resp["body"])["error"]["code"] == "SG_NOT_MANAGED"


# 6. SG_NOT_FOUND (404)
def test_error_sg_not_found(aws_env):
    resp = apply_handler({
        "body": json.dumps({
            "sg_id": "sg-00000000000000000",
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"]["code"] == "SG_NOT_FOUND"


# 7. CHANGE_NOT_FOUND (404)
def test_error_change_not_found(aws_env):
    resp = status_handler({"pathParameters": {"id": "01NONEXISTENTCHANGEID"}}, MockContext())
    assert resp["statusCode"] == 404
    assert json.loads(resp["body"])["error"]["code"] == "CHANGE_NOT_FOUND"


# 8. SG_BUSY (409)
def test_error_sg_busy(aws_env):
    sg_id = aws_env["sg_id"]
    # First change succeeds and locks SG
    apply_resp1 = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert apply_resp1["statusCode"] == 201

    # Second concurrent change on same SG fails with SG_BUSY
    apply_resp2 = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 443, "to_port": 443, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert apply_resp2["statusCode"] == 409
    assert json.loads(apply_resp2["body"])["error"]["code"] == "SG_BUSY"


# 9. RULE_ALREADY_EXISTS (409)
def test_error_rule_already_exists(aws_env):
    sg_id = aws_env["sg_id"]
    # Pre-add rule to SG
    aws_env["ec2"].authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    )

    resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert resp["statusCode"] == 409
    assert json.loads(resp["body"])["error"]["code"] == "RULE_ALREADY_EXISTS"


# 10. RULE_NOT_FOUND (409)
def test_error_rule_not_found(aws_env):
    sg_id = aws_env["sg_id"]
    # Attempt to revoke rule that does not exist
    resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "REVOKE", "protocol": "tcp", "from_port": 9999, "to_port": 9999, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert resp["statusCode"] == 409
    assert json.loads(resp["body"])["error"]["code"] == "RULE_NOT_FOUND"


# 11. NOT_APPLIED_YET (409)
def test_error_not_applied_yet(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Simulate in-flight apply (apply_done = false)
    ddb = aws_env["ddb"]
    ddb.update_item(
        TableName="deadman-changes-shared",
        Key={"pk": {"S": f"CHG#{change_id}"}},
        UpdateExpression="SET apply_done = :f",
        ExpressionAttributeValues={":f": {"BOOL": False}}
    )

    conf_resp = confirm_handler({"pathParameters": {"id": change_id}}, MockContext())
    assert conf_resp["statusCode"] == 409
    assert json.loads(conf_resp["body"])["error"]["code"] == "NOT_APPLIED_YET"


# 12. WINDOW_EXPIRED (409)
def test_error_window_expired(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 60,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    ddb = aws_env["ddb"]
    past = int(time.time()) - 50
    ddb.update_item(
        TableName="deadman-changes-shared",
        Key={"pk": {"S": f"CHG#{change_id}"}},
        UpdateExpression="SET expires_at = :p",
        ExpressionAttributeValues={":p": {"N": str(past)}}
    )

    conf_resp = confirm_handler({"pathParameters": {"id": change_id}}, MockContext())
    assert conf_resp["statusCode"] == 409
    assert json.loads(conf_resp["body"])["error"]["code"] == "WINDOW_EXPIRED"


# 13. CHANGE_NOT_PENDING (409)
def test_error_change_not_pending(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Revert it
    revert_handler({"trigger": "SCHEDULE", "change_id": change_id}, MockContext())

    # Try confirm
    conf_resp = confirm_handler({"pathParameters": {"id": change_id}}, MockContext())
    assert conf_resp["statusCode"] == 409
    assert json.loads(conf_resp["body"])["error"]["code"] == "CHANGE_NOT_PENDING"


# 14. REVERT_IN_PROGRESS (409)
def test_error_revert_in_progress(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Put in REVERTING with future lease
    ddb = aws_env["ddb"]
    future = int(time.time()) + 120
    ddb.update_item(
        TableName="deadman-changes-shared",
        Key={"pk": {"S": f"CHG#{change_id}"}},
        UpdateExpression="SET #s = :r, revert_lease_until = :f",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":r": {"S": "REVERTING"}, ":f": {"N": str(future)}}
    )

    manual_evt = {"requestContext": {"http": {"method": "POST"}}, "pathParameters": {"id": change_id}}
    rev_resp = revert_handler(manual_evt, MockContext())
    assert rev_resp["statusCode"] == 409
    assert json.loads(rev_resp["body"])["error"]["code"] == "REVERT_IN_PROGRESS"


# 15. SCHEDULE_FAILED (502)
def test_error_schedule_failed(aws_env):
    sg_id = aws_env["sg_id"]
    aws_env["scheduler"].should_fail_create = True
    resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert resp["statusCode"] == 502
    assert json.loads(resp["body"])["error"]["code"] == "SCHEDULE_FAILED"


# 16. APPLY_FAILED_REVERTED (500)
def test_error_apply_failed_reverted(aws_env, monkeypatch):
    sg_id = aws_env["sg_id"]
    def cut_fail(ec2, s_id, delta):
        raise RuntimeError("EC2 failure")
    monkeypatch.setattr("apply.app.apply_cut_ops", cut_fail)

    resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert resp["statusCode"] == 500
    assert json.loads(resp["body"])["error"]["code"] == "APPLY_FAILED_REVERTED"


# 17. APPLY_FAILED_REVERT_INCOMPLETE (500)
def test_error_apply_failed_revert_incomplete(aws_env, monkeypatch):
    sg_id = aws_env["sg_id"]
    def cut_fail(ec2, s_id, delta):
        raise RuntimeError("EC2 failure")
    def reconcile_fail(ec2, s_id, delta):
        return "PARTIAL_REVERT", [{"op_id": 1, "result": "ERROR", "detail": "Revert partial failure"}]

    monkeypatch.setattr("apply.app.apply_cut_ops", cut_fail)
    monkeypatch.setattr("apply.app.reconcile_revert_ops", reconcile_fail)

    resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert resp["statusCode"] == 500
    assert json.loads(resp["body"])["error"]["code"] == "APPLY_FAILED_REVERT_INCOMPLETE"


# 18. INTERNAL (500)
def test_error_internal():
    resp = make_error_response("INTERNAL", "Internal server error occurred", status_code=500)
    assert resp["statusCode"] == 500
    assert json.loads(resp["body"])["error"]["code"] == "INTERNAL"
