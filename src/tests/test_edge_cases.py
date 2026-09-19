"""
Unit tests covering EVERY ROW of the Spec §4 Edge-Case Table.
"""
import json
import time
import pytest
from conftest import MockContext
from apply.app import handler as apply_handler
from confirm.app import handler as confirm_handler
from revert.app import handler as revert_handler
from common.ddb import transact_put_change_and_lock, t1_fence, t3_start_revert
from common.errors import DeadmanError
from common.states import STATUS_PENDING, STATUS_CONFIRMED, STATUS_REVERTING, STATUS_REVERTED, STATUS_FAILED


# 1. Late confirm
def test_edge_case_late_confirm(aws_env):
    sg_id = aws_env["sg_id"]
    # Create change with ttl = 60
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 60,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Manually expire the change in DynamoDB by setting expires_at to past
    ddb = aws_env["ddb"]
    past_ts = int(time.time()) - 10
    ddb.update_item(
        TableName="deadman-changes-shared",
        Key={"pk": {"S": f"CHG#{change_id}"}},
        UpdateExpression="SET expires_at = :past",
        ExpressionAttributeValues={":past": {"N": str(past_ts)}}
    )

    conf_resp = confirm_handler({"pathParameters": {"id": change_id}}, MockContext())
    assert conf_resp["statusCode"] == 409
    body = json.loads(conf_resp["body"])
    assert body["error"]["code"] == "WINDOW_EXPIRED"


# 2. Double confirm
def test_edge_case_double_confirm(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    resp1 = confirm_handler({"pathParameters": {"id": change_id}}, MockContext())
    assert resp1["statusCode"] == 200
    assert json.loads(resp1["body"])["idempotent"] is False

    resp2 = confirm_handler({"pathParameters": {"id": change_id}}, MockContext())
    assert resp2["statusCode"] == 200
    assert json.loads(resp2["body"])["idempotent"] is True


# 3. Scheduled revert after confirm
def test_edge_case_scheduled_revert_after_confirm(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    confirm_handler({"pathParameters": {"id": change_id}}, MockContext())

    # Scheduled revert fires after confirmation
    sched_resp = revert_handler({"trigger": "SCHEDULE", "change_id": change_id}, MockContext())
    assert sched_resp["statusCode"] == 200
    assert json.loads(sched_resp["body"])["status"] == "NOOP"


# 4. Confirm vs revert race
def test_edge_case_confirm_vs_revert_race(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Revert wins first
    revert_handler({"trigger": "SCHEDULE", "change_id": change_id}, MockContext())

    # Confirm loses race: gets CHANGE_NOT_PENDING (409)
    conf_resp = confirm_handler({"pathParameters": {"id": change_id}}, MockContext())
    assert conf_resp["statusCode"] == 409
    body = json.loads(conf_resp["body"])
    assert body["error"]["code"] == "CHANGE_NOT_PENDING"
    assert body["error"]["status"] == "REVERTED"


# 5. Manual revert when T3 fails
def test_edge_case_manual_revert_when_t3_fails(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # 5a: PENDING with live apply lease -> 409 NOT_APPLIED_YET
    ddb = aws_env["ddb"]
    future_lease = int(time.time()) + 100
    ddb.update_item(
        TableName="deadman-changes-shared",
        Key={"pk": {"S": f"CHG#{change_id}"}},
        UpdateExpression="SET apply_done = :f, apply_lease_until = :l",
        ExpressionAttributeValues={":f": {"BOOL": False}, ":l": {"N": str(future_lease)}}
    )
    manual_evt = {
        "requestContext": {"http": {"method": "POST"}},
        "pathParameters": {"id": change_id}
    }
    resp_pending_lease = revert_handler(manual_evt, MockContext())
    assert resp_pending_lease["statusCode"] == 409
    assert json.loads(resp_pending_lease["body"])["error"]["code"] == "NOT_APPLIED_YET"

    # 5b: REVERTING with live lease -> 409 REVERT_IN_PROGRESS
    ddb.update_item(
        TableName="deadman-changes-shared",
        Key={"pk": {"S": f"CHG#{change_id}"}},
        UpdateExpression="SET #s = :r, revert_lease_until = :l",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":r": {"S": "REVERTING"}, ":l": {"N": str(future_lease)}}
    )
    resp_reverting = revert_handler(manual_evt, MockContext())
    assert resp_reverting["statusCode"] == 409
    assert json.loads(resp_reverting["body"])["error"]["code"] == "REVERT_IN_PROGRESS"

    # 5c: CONFIRMED -> 409 CHANGE_NOT_PENDING
    ddb.update_item(
        TableName="deadman-changes-shared",
        Key={"pk": {"S": f"CHG#{change_id}"}},
        UpdateExpression="SET #s = :c",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":c": {"S": "CONFIRMED"}}
    )
    resp_confirmed = revert_handler(manual_evt, MockContext())
    assert resp_confirmed["statusCode"] == 409
    assert json.loads(resp_confirmed["body"])["error"]["code"] == "CHANGE_NOT_PENDING"

    # 5d: REVERTED -> 200 idempotent: true
    ddb.update_item(
        TableName="deadman-changes-shared",
        Key={"pk": {"S": f"CHG#{change_id}"}},
        UpdateExpression="SET #s = :rev, reverted_at = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":rev": {"S": "REVERTED"}, ":now": {"N": str(int(time.time()))}}
    )
    resp_reverted = revert_handler(manual_evt, MockContext())
    assert resp_reverted["statusCode"] == 200
    assert json.loads(resp_reverted["body"])["idempotent"] is True


# 6. Manual vs scheduled revert
def test_edge_case_manual_vs_scheduled_revert(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Manual revert wins
    manual_evt = {
        "requestContext": {"http": {"method": "POST"}},
        "pathParameters": {"id": change_id}
    }
    m_resp = revert_handler(manual_evt, MockContext())
    assert m_resp["statusCode"] == 200
    assert json.loads(m_resp["body"])["status"] == "REVERTED"

    # Scheduled revert fires later -> NOOP
    s_resp = revert_handler({"trigger": "SCHEDULE", "change_id": change_id}, MockContext())
    assert s_resp["statusCode"] == 200
    assert json.loads(s_resp["body"])["status"] == "NOOP"


# 7. Apply died before T1
def test_edge_case_apply_died_before_t1(aws_env):
    sg_id = aws_env["sg_id"]
    change_id = "01TESTDIEDBEFORET1"
    now_ts = int(time.time())

    # Simulate T0 stored, but apply process died before T1 (no apply_lease_until)
    change_data = {
        "change_id": change_id,
        "status": STATUS_PENDING,
        "sg_id": sg_id,
        "ttl_seconds": 90,
        "created_at": now_ts,
        "expires_at": now_ts + 90,
        "schedule_name": f"dm-{change_id}",
        "apply_done": False,
        "snapshot": [],
        "delta": [{"op_id": 1, "action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0", "applied": None}],
        "confirmed_at": None,
        "reverted_at": None,
        "revert_trigger": None,
        "revert_report": None,
        "failure_reason": None,
        "purge_at": now_ts + 90 + 604800,
    }
    transact_put_change_and_lock(
        ddb_client=aws_env["ddb"],
        table_name="deadman-changes-shared",
        change_data=change_data,
        sg_id=sg_id,
        lock_until=now_ts + 90 + 360,
        now_ts=now_ts,
    )

    # Revert fires: T3 allows revert (no apply_lease_until), reconcile is SKIPPED (not in SG), status -> REVERTED
    s_resp = revert_handler({"trigger": "SCHEDULE", "change_id": change_id}, MockContext())
    assert s_resp["statusCode"] == 200
    assert json.loads(s_resp["body"])["status"] == "REVERTED"


# 8. Apply loses to a revert
def test_edge_case_apply_loses_to_revert(aws_env):
    # If revert already took change into REVERTING before T1 fence runs
    ddb = aws_env["ddb"]
    change_id = "01TESTAPPLYLOSESTO"
    now_ts = int(time.time())
    change_data = {
        "change_id": change_id,
        "status": STATUS_REVERTING,  # Revert already moved it
        "sg_id": aws_env["sg_id"],
        "ttl_seconds": 90,
        "created_at": now_ts,
        "expires_at": now_ts + 90,
        "schedule_name": f"dm-{change_id}",
        "apply_done": False,
        "snapshot": [],
        "delta": [],
        "confirmed_at": None,
        "reverted_at": None,
        "revert_trigger": "SCHEDULE",
        "revert_report": None,
        "failure_reason": None,
        "purge_at": now_ts + 90 + 604800,
    }
    transact_put_change_and_lock(
        ddb_client=ddb,
        table_name="deadman-changes-shared",
        change_data=change_data,
        sg_id=aws_env["sg_id"],
        lock_until=now_ts + 90 + 360,
        now_ts=now_ts,
    )

    # When T1 fence is attempted, it must raise CHANGE_NOT_PENDING
    with pytest.raises(DeadmanError) as exc_info:
        t1_fence(ddb, "deadman-changes-shared", change_id, now_ts)
    assert exc_info.value.code == "CHANGE_NOT_PENDING"


# 9. Op is a no-op at cut time
def test_edge_case_noop_at_cut_time(aws_env):
    sg_id = aws_env["sg_id"]
    # Authorize port 80 outside of Deadman right after planning
    ec2 = aws_env["ec2"]
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    )

    # In our rules module, apply_cut_ops marks op applied=False when duplicate error occurs
    from common.rules import apply_cut_ops
    delta = [{"op_id": 1, "action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0", "applied": None}]
    updated = apply_cut_ops(ec2, sg_id, delta)
    assert updated[0]["applied"] is False


# 10. Third-party edit in window
def test_edge_case_third_party_edit_in_window(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Third party adds an unrelated rule (port 22)
    ec2 = aws_env["ec2"]
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    )

    # Revert fires
    revert_handler({"trigger": "SCHEDULE", "change_id": change_id}, MockContext())

    # Third-party port 22 is untouched, port 80 is removed
    rules = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]["IpPermissions"]
    ports = [p.get("FromPort") for p in rules]
    assert 22 in ports
    assert 80 not in ports


# 11. Rule already deleted or re-added
def test_edge_case_rule_already_deleted(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Someone manually deleted port 80 before revert ran
    ec2 = aws_env["ec2"]
    ec2.revoke_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    )

    rev_resp = revert_handler({"trigger": "SCHEDULE", "change_id": change_id}, MockContext())
    assert rev_resp["statusCode"] == 200
    assert json.loads(rev_resp["body"])["status"] == "REVERTED"


# 12. Schedule creation failure
def test_edge_case_schedule_creation_failure(aws_env):
    sg_id = aws_env["sg_id"]
    aws_env["scheduler"].should_fail_create = True
    aws_env["scheduler"].create_error_code = "InternalServerException"

    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert apply_resp["statusCode"] == 502
    body = json.loads(apply_resp["body"])
    assert body["error"]["code"] == "SCHEDULE_FAILED"
    assert body["error"]["status"] == "FAILED"

    # Verify no rule was cut
    rules = aws_env["ec2"].describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]["IpPermissions"]
    assert len(rules) == 0


# 13. Apply failure after arming
def test_edge_case_apply_failure_after_arming(aws_env, monkeypatch):
    sg_id = aws_env["sg_id"]

    # Monkeypatch apply_cut_ops to simulate hard failure during cut
    def mock_cut_fail(ec2_client, s_id, delta):
        raise RuntimeError("EC2 network outage during cut")

    monkeypatch.setattr("apply.app.apply_cut_ops", mock_cut_fail)

    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    assert apply_resp["statusCode"] == 500
    body = json.loads(apply_resp["body"])
    assert body["error"]["code"] == "APPLY_FAILED_REVERTED"


# 14. Duplicate Scheduler delivery (live lease -> NOOP, expired lease -> T4 takeover)
def test_edge_case_duplicate_scheduler_delivery(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Simulate active revert lease in DynamoDB
    ddb = aws_env["ddb"]
    future_lease = int(time.time()) + 100
    ddb.update_item(
        TableName="deadman-changes-shared",
        Key={"pk": {"S": f"CHG#{change_id}"}},
        UpdateExpression="SET #s = :r, revert_lease_until = :l, revert_owner = :o",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":r": {"S": "REVERTING"},
            ":l": {"N": str(future_lease)},
            ":o": {"S": "worker-1"},
        }
    )

    # Duplicate delivery with live lease -> NOOP (doesn't raise or interfere)
    dup_resp = revert_handler({"trigger": "SCHEDULE", "change_id": change_id}, MockContext("worker-2"))
    assert dup_resp["statusCode"] == 200
    assert json.loads(dup_resp["body"])["status"] == "NOOP"

    # Now expire the lease
    past_lease = int(time.time()) - 10
    ddb.update_item(
        TableName="deadman-changes-shared",
        Key={"pk": {"S": f"CHG#{change_id}"}},
        UpdateExpression="SET revert_lease_until = :l",
        ExpressionAttributeValues={":l": {"N": str(past_lease)}}
    )

    # Next delivery performs T4 takeover and completes revert!
    takeover_resp = revert_handler({"trigger": "SCHEDULE", "change_id": change_id}, MockContext("worker-3"))
    assert takeover_resp["statusCode"] == 200
    assert json.loads(takeover_resp["body"])["status"] == "REVERTED"


# 15. Tag removed mid-window
def test_edge_case_tag_removed_mid_window(aws_env):
    sg_id = aws_env["sg_id"]
    apply_resp = apply_handler({
        "body": json.dumps({
            "sg_id": sg_id,
            "ttl_seconds": 90,
            "ops": [{"action": "AUTHORIZE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}]
        })
    }, MockContext())
    change_id = json.loads(apply_resp["body"])["change_id"]

    # Tag removed mid-window
    ec2 = aws_env["ec2"]
    ec2.delete_tags(
        Resources=[sg_id],
        Tags=[{"Key": "deadman:managed"}]
    )

    # Manual revert attempted
    manual_evt = {
        "requestContext": {"http": {"method": "POST"}},
        "pathParameters": {"id": change_id}
    }
    rev_resp = revert_handler(manual_evt, MockContext())
    assert rev_resp["statusCode"] == 200
    body = json.loads(rev_resp["body"])
    assert body["status"] == "FAILED"
    assert "tag check failed" in body["revert_report"][0]["detail"]
