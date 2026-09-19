"""
Unit tests for apply error handling, catch-all crash safety, lock release, and fail-fast env validation.
Spec §2, §3, §4, §5.
"""
import os
import json
import pytest
import botocore.exceptions
from conftest import MockContext
from apply.app import handler as apply_handler
from confirm.app import handler as confirm_handler
from revert.app import handler as revert_handler
from status.app import handler as status_handler


def test_apply_create_schedule_param_validation_error(aws_env):
    """
    When create_schedule raises ParamValidationError (e.g. empty Target.Arn),
    the handler must catch it, transition the item to FAILED, release the SGLOCK,
    and return 502 SCHEDULE_FAILED with the exception class.
    """
    sg_id = aws_env["sg_id"]
    ddb = aws_env["ddb"]
    table_name = os.environ["TABLE_NAME"]

    # Inject ParamValidationError in MockSchedulerClient
    aws_env["scheduler"].create_exception = botocore.exceptions.ParamValidationError(
        report="Parameter validation failed: Missing required parameter in Target: 'Arn'"
    )

    req_body = {
        "sg_id": sg_id,
        "ttl_seconds": 90,
        "ops": [
            {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}
        ]
    }
    resp = apply_handler({"body": json.dumps(req_body)}, MockContext())

    # Must return 502 SCHEDULE_FAILED
    assert resp["statusCode"] == 502
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "SCHEDULE_FAILED"
    assert body["error"]["exception"] == "ParamValidationError"
    assert "ParamValidationError" in body["error"]["message"]
    change_id = body["error"]["change_id"]
    assert change_id

    # Item in DynamoDB must be FAILED, not PENDING
    item_resp = ddb.get_item(TableName=table_name, Key={"pk": {"S": f"CHG#{change_id}"}})
    assert "Item" in item_resp
    item = item_resp["Item"]
    assert item["status"]["S"] == "FAILED"
    assert item["apply_done"]["BOOL"] is False
    assert "ParamValidationError" in item.get("failure_reason", {}).get("S", "")

    # SGLOCK must be released (lock item deleted)
    lock_resp = ddb.get_item(TableName=table_name, Key={"pk": {"S": f"SGLOCK#{sg_id}"}})
    assert "Item" not in lock_resp

    # Subsequent apply on the same SG must succeed because the lock was released
    aws_env["scheduler"].create_exception = None
    resp2 = apply_handler({"body": json.dumps(req_body)}, MockContext())
    assert resp2["statusCode"] == 201


def test_apply_create_schedule_runtime_error(aws_env):
    """
    When create_schedule raises an unexpected RuntimeError (any type, not only ClientError),
    the handler must catch it, transition the item to FAILED, release the SGLOCK,
    and return 502 SCHEDULE_FAILED with the exception class.
    """
    sg_id = aws_env["sg_id"]
    ddb = aws_env["ddb"]
    table_name = os.environ["TABLE_NAME"]

    # Inject RuntimeError
    aws_env["scheduler"].create_exception = RuntimeError("Connection dropped by scheduler service")

    req_body = {
        "sg_id": sg_id,
        "ttl_seconds": 90,
        "ops": [
            {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}
        ]
    }
    resp = apply_handler({"body": json.dumps(req_body)}, MockContext())

    # Must return 502 SCHEDULE_FAILED
    assert resp["statusCode"] == 502
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "SCHEDULE_FAILED"
    assert body["error"]["exception"] == "RuntimeError"
    assert "RuntimeError" in body["error"]["message"]
    change_id = body["error"]["change_id"]
    assert change_id

    # Item in DynamoDB must be FAILED, not PENDING
    item_resp = ddb.get_item(TableName=table_name, Key={"pk": {"S": f"CHG#{change_id}"}})
    assert "Item" in item_resp
    item = item_resp["Item"]
    assert item["status"]["S"] == "FAILED"
    assert item["apply_done"]["BOOL"] is False
    assert "RuntimeError" in item.get("failure_reason", {}).get("S", "")

    # SGLOCK must be released
    lock_resp = ddb.get_item(TableName=table_name, Key={"pk": {"S": f"SGLOCK#{sg_id}"}})
    assert "Item" not in lock_resp

    # Subsequent apply on the same SG must succeed
    aws_env["scheduler"].create_exception = None
    resp2 = apply_handler({"body": json.dumps(req_body)}, MockContext())
    assert resp2["statusCode"] == 201


def test_sg_busy_includes_active_change_id(aws_env):
    """
    When an apply fails with SG_BUSY, the error JSON must include
    the change_id of the holding change.
    """
    sg_id = aws_env["sg_id"]
    req_body1 = {
        "sg_id": sg_id,
        "ttl_seconds": 90,
        "ops": [
            {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}
        ]
    }
    resp1 = apply_handler({"body": json.dumps(req_body1)}, MockContext())
    assert resp1["statusCode"] == 201
    change_id_1 = json.loads(resp1["body"])["change_id"]

    req_body2 = {
        "sg_id": sg_id,
        "ttl_seconds": 90,
        "ops": [
            {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 443, "to_port": 443, "cidr": "0.0.0.0/0"}
        ]
    }
    resp2 = apply_handler({"body": json.dumps(req_body2)}, MockContext())
    assert resp2["statusCode"] == 409
    body2 = json.loads(resp2["body"])
    assert body2["error"]["code"] == "SG_BUSY"
    assert body2["error"]["change_id"] == change_id_1


@pytest.mark.parametrize("var_to_clear", ["TABLE_NAME", "SCHEDULE_GROUP", "REVERT_FN_ARN", "SCHEDULER_ROLE_ARN", "STAGE"])
def test_apply_missing_env_vars(aws_env, monkeypatch, var_to_clear):
    """Fail-fast check: missing or empty required env var in apply returns 500 INTERNAL naming the variable."""
    monkeypatch.delenv(var_to_clear, raising=False)
    resp = apply_handler({"body": json.dumps({"sg_id": "sg-123", "ttl_seconds": 90, "ops": []})}, MockContext())
    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INTERNAL"
    assert var_to_clear in body["error"]["message"]


@pytest.mark.parametrize("var_to_clear", ["TABLE_NAME", "SCHEDULE_GROUP"])
def test_confirm_missing_env_vars(aws_env, monkeypatch, var_to_clear):
    """Fail-fast check: missing or empty required env var in confirm returns 500 INTERNAL naming the variable."""
    monkeypatch.delenv(var_to_clear, raising=False)
    resp = confirm_handler({"pathParameters": {"id": "01TEST"}}, MockContext())
    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INTERNAL"
    assert var_to_clear in body["error"]["message"]


@pytest.mark.parametrize("var_to_clear", ["TABLE_NAME", "SCHEDULE_GROUP", "STAGE"])
def test_revert_missing_env_vars(aws_env, monkeypatch, var_to_clear):
    """Fail-fast check: missing or empty required env var in revert returns 500 INTERNAL naming the variable."""
    monkeypatch.delenv(var_to_clear, raising=False)
    event = {
        "requestContext": {"http": {"method": "POST", "path": "/api/changes/01TEST/revert"}},
        "pathParameters": {"id": "01TEST"},
    }
    resp = revert_handler(event, MockContext())
    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INTERNAL"
    assert var_to_clear in body["error"]["message"]


def test_status_missing_env_vars(aws_env, monkeypatch):
    """Fail-fast check: missing or empty TABLE_NAME in status returns 500 INTERNAL naming the variable."""
    monkeypatch.delenv("TABLE_NAME", raising=False)
    resp = status_handler({"pathParameters": {"id": "01TEST"}}, MockContext())
    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert body["error"]["code"] == "INTERNAL"
    assert "TABLE_NAME" in body["error"]["message"]
