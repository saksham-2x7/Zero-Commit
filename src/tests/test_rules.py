"""
Unit tests for security group rule logic, delta planning, and reconciliation.
"""
import pytest
from moto import mock_aws

from common.rules import (
    make_tuple_key,
    describe_sg_rules,
    plan_delta,
    apply_cut_ops,
    reconcile_revert_ops,
)
from common.errors import DeadmanError
from common.states import RESULT_REVERTED, RESULT_SKIPPED, RESULT_ERROR


def test_tuple_key_normalization():
    k1 = make_tuple_key("TCP", 80, 80, "CIDR", "0.0.0.0/0")
    k2 = make_tuple_key("tcp", 80, 80, "cidr", "0.0.0.0/0")
    assert k1 == k2 == "tcp/80/80/cidr/0.0.0.0/0"

    k3 = make_tuple_key("-1", 0, 0, "cidr", "10.0.0.0/8")
    assert k3 == "-1/-1/-1/cidr/10.0.0.0/8"


def test_plan_delta_effective_ops():
    snapshot = [
        {"tuple_key": "tcp/80/80/cidr/0.0.0.0/0", "protocol": "tcp", "from_port": 80, "to_port": 80, "source": "0.0.0.0/0"}
    ]

    # Authorize absent rule & revoke present rule
    req = [
        {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 443, "to_port": 443, "cidr": "0.0.0.0/0"},
        {"action": "REVOKE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"},
    ]

    delta = plan_delta(snapshot, req)
    assert len(delta) == 2
    assert delta[0]["op_id"] == 1
    assert delta[0]["action"] == "AUTHORIZE"
    assert delta[0]["applied"] is None
    assert delta[1]["op_id"] == 2
    assert delta[1]["action"] == "REVOKE"
    assert delta[1]["applied"] is None


def test_plan_delta_rule_already_exists():
    snapshot = [
        {"tuple_key": "tcp/80/80/cidr/0.0.0.0/0", "protocol": "tcp", "from_port": 80, "to_port": 80, "source": "0.0.0.0/0"}
    ]
    req = [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]

    with pytest.raises(DeadmanError) as exc_info:
        plan_delta(snapshot, req)
    assert exc_info.value.code == "RULE_ALREADY_EXISTS"
    assert exc_info.value.status_code == 409


def test_plan_delta_rule_not_found():
    snapshot = []
    req = [{"action": "REVOKE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]

    with pytest.raises(DeadmanError) as exc_info:
        plan_delta(snapshot, req)
    assert exc_info.value.code == "RULE_NOT_FOUND"
    assert exc_info.value.status_code == 409


def test_plan_delta_duplicate_in_request():
    snapshot = []
    req = [
        {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"},
        {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"},
    ]
    with pytest.raises(DeadmanError) as exc_info:
        plan_delta(snapshot, req)
    assert exc_info.value.code == "INVALID_REQUEST"
    assert exc_info.value.status_code == 400


def test_apply_cut_ops_and_reconcile(aws_env):
    ec2 = aws_env["ec2"]
    sg_id = aws_env["sg_id"]

    # Initial state: authorize port 80
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    )

    # Cut ops: Authorize 443 and Revoke 80
    delta = [
        {"op_id": 1, "action": "AUTHORIZE", "protocol": "tcp", "from_port": 443, "to_port": 443, "cidr": "0.0.0.0/0", "applied": None},
        {"op_id": 2, "action": "REVOKE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0", "applied": None},
    ]

    updated_delta = apply_cut_ops(ec2, sg_id, delta)
    assert updated_delta[0]["applied"] is True
    assert updated_delta[1]["applied"] is True

    # Check EC2: port 443 should exist, port 80 should not
    rules = describe_sg_rules(ec2, sg_id)
    keys = {r["tuple_key"] for r in rules}
    assert "tcp/443/443/cidr/0.0.0.0/0" in keys
    assert "tcp/80/80/cidr/0.0.0.0/0" not in keys

    # Now reconcile (revert): should undo in reverse order
    status, report = reconcile_revert_ops(ec2, sg_id, updated_delta)
    assert status == "REVERTED"
    assert len(report) == 2
    assert report[0]["result"] == RESULT_REVERTED  # op 1 undone (revoked 443)
    assert report[1]["result"] == RESULT_REVERTED  # op 2 undone (authorized 80)

    # Check EC2 again: port 80 restored, port 443 removed
    rules_after = describe_sg_rules(ec2, sg_id)
    keys_after = {r["tuple_key"] for r in rules_after}
    assert "tcp/80/80/cidr/0.0.0.0/0" in keys_after
    assert "tcp/443/443/cidr/0.0.0.0/0" not in keys_after


def test_reconcile_honors_applied_false(aws_env):
    ec2 = aws_env["ec2"]
    sg_id = aws_env["sg_id"]

    delta = [
        {"op_id": 1, "action": "AUTHORIZE", "protocol": "tcp", "from_port": 8080, "to_port": 8080, "cidr": "0.0.0.0/0", "applied": False},
    ]

    status, report = reconcile_revert_ops(ec2, sg_id, delta)
    assert status == "REVERTED"
    assert report[0]["result"] == RESULT_SKIPPED
    assert "applied=false" in report[0]["detail"]


def test_reconcile_rule_already_deleted_or_readded(aws_env):
    ec2 = aws_env["ec2"]
    sg_id = aws_env["sg_id"]

    # Op 1: was AUTHORIZE 8080 (applied=True), but someone deleted 8080 during the window
    delta = [
        {"op_id": 1, "action": "AUTHORIZE", "protocol": "tcp", "from_port": 8080, "to_port": 8080, "cidr": "0.0.0.0/0", "applied": True},
    ]

    status, report = reconcile_revert_ops(ec2, sg_id, delta)
    # Not present when attempting to revoke -> SKIPPED_ALREADY_SATISFIED -> overall REVERTED
    assert status == "REVERTED"
    assert report[0]["result"] == RESULT_SKIPPED
