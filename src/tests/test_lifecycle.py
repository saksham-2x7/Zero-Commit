"""
Unit tests for Deadman complete lifecycles: Apply, Confirm, Revert, and Status.
"""
import json
import pytest
from conftest import MockContext
from apply.app import handler as apply_handler
from confirm.app import handler as confirm_handler
from revert.app import handler as revert_handler
from status.app import handler as status_handler
from common.rules import describe_sg_rules


def test_apply_confirm_lifecycle(aws_env):
    sg_id = aws_env["sg_id"]

    # 1. POST /api/changes
    req_body = {
        "sg_id": sg_id,
        "ttl_seconds": 90,
        "ops": [
            {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}
        ]
    }
    apply_resp = apply_handler({"body": json.dumps(req_body)}, MockContext())
    assert apply_resp["statusCode"] == 201
    apply_data = json.loads(apply_resp["body"])
    change_id = apply_data["change_id"]
    assert apply_data["status"] == "PENDING"
    assert apply_data["delta"][0]["applied"] is True

    # Check EC2 rule applied
    rules = describe_sg_rules(aws_env["ec2"], sg_id)
    assert any(r["from_port"] == 80 for r in rules)

    # 2. GET /api/changes/{id}
    st_resp = status_handler({"pathParameters": {"id": change_id}}, MockContext())
    assert st_resp["statusCode"] == 200
    st_data = json.loads(st_resp["body"])
    assert st_data["status"] == "PENDING"
    assert st_data["apply_done"] is True

    # 3. POST /api/changes/{id}/confirm
    conf_resp = confirm_handler({"pathParameters": {"id": change_id}}, MockContext())
    assert conf_resp["statusCode"] == 200
    conf_data = json.loads(conf_resp["body"])
    assert conf_data["status"] == "CONFIRMED"
    assert conf_data["idempotent"] is False

    # 4. Double confirm (idempotent 200)
    conf_resp2 = confirm_handler({"pathParameters": {"id": change_id}}, MockContext())
    assert conf_resp2["statusCode"] == 200
    conf_data2 = json.loads(conf_resp2["body"])
    assert conf_data2["status"] == "CONFIRMED"
    assert conf_data2["idempotent"] is True

    # Confirm schedule was deleted
    assert f"dm-{change_id}" not in aws_env["scheduler"].schedules


def test_apply_scheduled_revert_lifecycle(aws_env):
    sg_id = aws_env["sg_id"]

    # 1. Apply
    req_body = {
        "sg_id": sg_id,
        "ttl_seconds": 90,
        "ops": [
            {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 8080, "to_port": 8080, "cidr": "0.0.0.0/0"}
        ]
    }
    apply_resp = apply_handler({"body": json.dumps(req_body)}, MockContext())
    assert apply_resp["statusCode"] == 201
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Rule is present
    rules = describe_sg_rules(aws_env["ec2"], sg_id)
    assert any(r["from_port"] == 8080 for r in rules)

    # 2. EventBridge Scheduler triggers revert
    sched_event = {
        "trigger": "SCHEDULE",
        "change_id": change_id,
    }
    rev_resp = revert_handler(sched_event, MockContext())
    assert rev_resp["statusCode"] == 200
    rev_data = json.loads(rev_resp["body"])
    assert rev_data["status"] == "REVERTED"

    # Rule is gone
    rules_after = describe_sg_rules(aws_env["ec2"], sg_id)
    assert not any(r["from_port"] == 8080 for r in rules_after)

    # 3. Check status
    st_resp = status_handler({"pathParameters": {"id": change_id}}, MockContext())
    st_data = json.loads(st_resp["body"])
    assert st_data["status"] == "REVERTED"
    assert st_data["revert_trigger"] == "SCHEDULE"
    assert st_data["reverted_at"] is not None


def test_apply_manual_revert_lifecycle(aws_env):
    sg_id = aws_env["sg_id"]

    # 1. Apply
    req_body = {
        "sg_id": sg_id,
        "ttl_seconds": 90,
        "ops": [
            {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 3000, "to_port": 3000, "cidr": "0.0.0.0/0"}
        ]
    }
    apply_resp = apply_handler({"body": json.dumps(req_body)}, MockContext())
    assert apply_resp["statusCode"] == 201
    change_id = json.loads(apply_resp["body"])["change_id"]

    # 2. POST /api/changes/{id}/revert (manual revert)
    manual_event = {
        "requestContext": {"http": {"method": "POST", "path": f"/api/changes/{change_id}/revert"}},
        "pathParameters": {"id": change_id},
    }
    rev_resp = revert_handler(manual_event, MockContext())
    assert rev_resp["statusCode"] == 200
    rev_data = json.loads(rev_resp["body"])
    assert rev_data["status"] == "REVERTED"
    assert rev_data["revert_trigger"] == "MANUAL"
    assert rev_data["idempotent"] is False

    # 3. Double manual revert (idempotent 200)
    rev_resp2 = revert_handler(manual_event, MockContext())
    assert rev_resp2["statusCode"] == 200
    rev_data2 = json.loads(rev_resp2["body"])
    assert rev_data2["status"] == "REVERTED"
    assert rev_data2["idempotent"] is True
