"""
Request-shape checks: run the Lambda handlers against real boto3 clients wrapped
in botocore Stubber. Client-side ParamValidator validates every call against the
real AWS API models, so any parameter typo raises ParamValidationError (which
mocks like moto can miss). Calls are also recorded and spec-critical values are
asserted.

Run: python -m pytest src/tests/test_request_shapes.py -q
"""
import os
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.stub import Stubber
from boto3.dynamodb.types import TypeSerializer

from common.aws import set_ec2_client, set_dynamodb_client, set_scheduler_client, reset_clients
from apply.app import handler as apply_handler
from confirm.app import handler as confirm_handler
from revert.app import handler as revert_handler

SG_ID = "sg-0abc1234def56789"
TABLE = "deadman-changes-shared"
GROUP = "deadman-shared"
TS = TypeSerializer()


def wrap(client, ops, recorded):
    for op in ops:
        orig = getattr(client, op)

        def make_wrapper(op=op, orig=orig):
            def wrapper(**kwargs):
                recorded.append((op, kwargs))
                return orig(**kwargs)
            return wrapper

        setattr(client, op, make_wrapper())


def make_clients():
    """Real clients (dummy creds) + Stubber + recording wrappers, injected."""
    recorded = []
    ec2 = boto3.client("ec2", region_name="ap-south-1")
    ddb = boto3.client("dynamodb", region_name="ap-south-1")
    sched = boto3.client("scheduler", region_name="ap-south-1")
    wrap(ec2, ["describe_security_groups", "authorize_security_group_ingress",
               "revoke_security_group_ingress"], recorded)
    wrap(ddb, ["transact_write_items", "update_item", "delete_item", "get_item"], recorded)
    wrap(sched, ["create_schedule", "delete_schedule"], recorded)
    st_ec2, st_ddb, st_sched = Stubber(ec2), Stubber(ddb), Stubber(sched)
    st_ec2.activate()
    st_ddb.activate()
    st_sched.activate()
    set_ec2_client(ec2)
    set_dynamodb_client(ddb)
    set_scheduler_client(sched)
    return ec2, ddb, sched, st_ec2, st_ddb, st_sched, recorded


def sg_response(tags=True):
    sgs = [{"GroupId": SG_ID}]
    if tags:
        sgs[0]["Tags"] = [
            {"Key": "deadman:managed", "Value": "true"},
            {"Key": "deadman:stage", "Value": "shared"},
        ]
    return {"SecurityGroups": sgs}


def rules_response(cidrs):
    rules = []
    for cidr in cidrs:
        rules.append({
            "SecurityGroupRuleId": f"sgr-{abs(hash(cidr)) % 10**10}",
            "GroupId": SG_ID,
            "GroupOwnerId": "123456789012",
            "IsEgress": False,
            "IpProtocol": "tcp",
            "FromPort": 443 if cidr == "0.0.0.0/0" else 22,
            "ToPort": 443 if cidr == "0.0.0.0/0" else 22,
            "CidrIpv4": cidr,
            "Description": "",
        })
    return {"SecurityGroupRules": rules}


def ddb_item(py_item):
    return {k: TS.serialize(v) for k, v in py_item.items()}


def change_item(change_id, status="REVERTING"):
    now_ts = int(time.time())
    delta = [
        {"op_id": 1, "action": "AUTHORIZE", "protocol": "tcp", "from_port": 443, "to_port": 443,
         "cidr": "0.0.0.0/0", "applied": True, "tuple_key": "tcp/443/443/cidr/0.0.0.0/0"},
        {"op_id": 2, "action": "AUTHORIZE", "protocol": "tcp", "from_port": 22, "to_port": 22,
         "cidr": "10.0.0.0/8", "applied": True, "tuple_key": "tcp/22/22/cidr/10.0.0.0/8"},
    ]
    return {
        "pk": f"CHG#{change_id}",
        "change_id": change_id,
        "status": status,
        "sg_id": SG_ID,
        "ttl_seconds": 300,
        "created_at": now_ts - 100,
        "expires_at": now_ts + 200,
        "schedule_name": f"dm-{change_id}",
        "apply_done": True,
        "apply_lease_until": now_ts - 80,
        "snapshot": [],
        "delta": delta,
        "confirmed_at": None,
        "reverted_at": None,
        "revert_trigger": "SCHEDULE",
        "revert_owner": "test-owner",
        "revert_lease_until": now_ts + 60,
        "revert_report": None,
        "failure_reason": None,
        "purge_at": now_ts + 604800,
    }


def apply_payload():
    return {
        "sg_id": SG_ID,
        "ttl_seconds": 300,
        "ops": [
            {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 443, "to_port": 443, "cidr": "0.0.0.0/0"},
            {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 22, "to_port": 22, "cidr": "10.0.0.0/8"},
        ],
    }


def calls(recorded, op):
    return [k for o, k in recorded if o == op]


def test_apply_request_shapes():
    reset_clients()
    ec2, ddb, sched, st_ec2, st_ddb, st_sched, recorded = make_clients()

    st_ec2.add_response("describe_security_groups", sg_response())
    st_ec2.add_response("describe_security_group_rules", rules_response([]))
    st_ddb.add_response("transact_write_items", {})
    st_sched.add_response("create_schedule", {
        "ScheduleArn": f"arn:aws:scheduler:ap-south-1:123456789012:schedule/{GROUP}/dm-test",
    })
    st_ddb.add_response("update_item", {})  # T1 fence
    st_ec2.add_response("authorize_security_group_ingress", {})
    st_ec2.add_response("authorize_security_group_ingress", {})
    st_ddb.add_response("update_item", {})  # T1b cut done

    resp = apply_handler({"body": json.dumps(apply_payload())}, None)
    body = json.loads(resp.get("body", "{}"))
    change_id = body.get("change_id", "")

    assert resp["statusCode"] == 201, resp
    assert change_id, "apply must return change_id"

    sc = calls(recorded, "create_schedule")
    assert len(sc) == 1
    assert sc[0]["Name"].startswith("dm-")
    assert sc[0]["GroupName"] == GROUP
    assert str(sc[0]["ScheduleExpression"]).startswith("at(")
    assert sc[0]["FlexibleTimeWindow"] == {"Mode": "OFF"}
    assert sc[0]["ActionAfterCompletion"] == "DELETE"
    assert set(sc[0]["Target"].keys()) == {"Arn", "RoleArn", "Input"}
    t_in = json.loads(sc[0]["Target"]["Input"])
    assert t_in == {"trigger": "SCHEDULE", "change_id": change_id}

    tw = calls(recorded, "transact_write_items")
    assert len(tw) == 1
    items = tw[0]["TransactItems"]
    assert len(items) == 2
    for it in items:
        put = it["Put"]
        assert put["TableName"] == TABLE
        assert put["ConditionExpression"]
        assert "pk" in put["Item"]

    auth = calls(recorded, "authorize_security_group_ingress")
    assert len(auth) == 2
    for ac in auth:
        assert ac["GroupId"] == SG_ID
        perm = ac["IpPermissions"][0]
        assert perm["IpProtocol"] == "tcp"
        assert perm["FromPort"] is not None and perm["ToPort"] is not None
        assert perm["IpRanges"][0]["CidrIp"]

    upd = calls(recorded, "update_item")
    assert len(upd) == 2  # T1 fence + T1b
    for uc in upd:
        assert uc["TableName"] == TABLE
        assert uc["Key"]["pk"]["S"].startswith("CHG#")
        assert uc["ConditionExpression"] and uc["UpdateExpression"]
        assert uc["ExpressionAttributeNames"]["#status"] == "status"

    assert calls(recorded, "delete_schedule") == []

    st_ec2.assert_no_pending_responses()
    st_ddb.assert_no_pending_responses()
    st_sched.assert_no_pending_responses()


def test_confirm_request_shapes():
    reset_clients()
    ec2, ddb, sched, st_ec2, st_ddb, st_sched, recorded = make_clients()
    change_id = "01M2WM82675KNFWZB1K3FV76FD"
    now_ts = int(time.time())
    item = {
        "pk": f"CHG#{change_id}",
        "status": "CONFIRMED",
        "sg_id": SG_ID,
        "schedule_name": f"dm-{change_id}",
        "confirmed_at": now_ts,
    }
    st_ddb.add_response("update_item", {"Attributes": ddb_item(item)})  # T2
    st_sched.add_response("delete_schedule", {})
    st_ddb.add_response("delete_item", {})  # lock release

    resp = confirm_handler({"pathParameters": {"id": change_id}}, None)
    body = json.loads(resp.get("body", "{}"))

    assert resp["statusCode"] == 200, resp
    assert body["status"] == "CONFIRMED"
    assert body["idempotent"] is False

    upd = calls(recorded, "update_item")
    assert len(upd) == 1
    assert upd[0]["TableName"] == TABLE
    assert upd[0]["Key"]["pk"]["S"] == f"CHG#{change_id}"
    assert upd[0]["ConditionExpression"] and upd[0]["UpdateExpression"]
    assert upd[0]["ExpressionAttributeNames"]["#status"] == "status"

    dels = calls(recorded, "delete_schedule")
    assert len(dels) == 1
    assert dels[0]["Name"] == f"dm-{change_id}"
    assert dels[0]["GroupName"] == GROUP

    lock_dels = calls(recorded, "delete_item")
    assert len(lock_dels) == 1
    assert lock_dels[0]["TableName"] == TABLE
    assert lock_dels[0]["Key"]["pk"]["S"] == f"SGLOCK#{SG_ID}"
    assert lock_dels[0]["ConditionExpression"] == "active_change_id = :cid"
    assert lock_dels[0]["ExpressionAttributeValues"][":cid"]["S"] == change_id

    st_ec2.assert_no_pending_responses()
    st_ddb.assert_no_pending_responses()
    st_sched.assert_no_pending_responses()


def _revert_scenario(manual):
    reset_clients()
    ec2, ddb, sched, st_ec2, st_ddb, st_sched, recorded = make_clients()
    change_id = "01M2WM82675KNFWZB1K3FV76FD"

    st_ddb.add_response("update_item", {"Attributes": ddb_item(change_item(change_id))})  # T3
    if manual:
        st_sched.add_response("delete_schedule", {})
    st_ec2.add_response("describe_security_groups", sg_response())
    st_ec2.add_response("describe_security_group_rules", rules_response(["0.0.0.0/0", "10.0.0.0/8"]))
    st_ec2.add_response("revoke_security_group_ingress", {})  # op2 (reverse order)
    st_ec2.add_response("describe_security_group_rules", rules_response(["0.0.0.0/0"]))
    st_ec2.add_response("revoke_security_group_ingress", {})  # op1
    st_ddb.add_response("update_item", {"Attributes": ddb_item({"pk": f"CHG#{change_id}", "status": "REVERTED"})})  # finish_revert
    st_ddb.add_response("delete_item", {})  # lock release

    if manual:
        event = {
            "requestContext": {"http": {"method": "POST", "path": f"/changes/{change_id}/revert"}},
            "pathParameters": {"id": change_id},
        }
    else:
        event = {"trigger": "SCHEDULE", "change_id": change_id}

    resp = revert_handler(event, None)
    body = json.loads(resp.get("body", "{}"))

    assert resp["statusCode"] == 200, resp
    assert body["status"] == "REVERTED"
    if manual:
        assert body["revert_trigger"] == "MANUAL"

    rev = calls(recorded, "revoke_security_group_ingress")
    assert len(rev) == 2
    for rc in rev:
        assert rc["GroupId"] == SG_ID
        perm = rc["IpPermissions"][0]
        assert perm["IpProtocol"] == "tcp"
        assert perm["FromPort"] is not None and perm["ToPort"] is not None
        assert perm["IpRanges"][0]["CidrIp"]

    dels = calls(recorded, "delete_schedule")
    if manual:
        assert len(dels) == 1
        assert dels[0]["Name"] == f"dm-{change_id}"
        assert dels[0]["GroupName"] == GROUP
    else:
        assert dels == []

    lock_dels = calls(recorded, "delete_item")
    assert len(lock_dels) == 1
    assert lock_dels[0]["Key"]["pk"]["S"] == f"SGLOCK#{SG_ID}"

    st_ec2.assert_no_pending_responses()
    st_ddb.assert_no_pending_responses()
    st_sched.assert_no_pending_responses()


def test_revert_scheduled_request_shapes():
    _revert_scenario(manual=False)


def test_revert_manual_request_shapes():
    _revert_scenario(manual=True)


def test_apply_schedule_failure_t8():
    reset_clients()
    ec2, ddb, sched, st_ec2, st_ddb, st_sched, recorded = make_clients()

    st_ec2.add_response("describe_security_groups", sg_response())
    st_ec2.add_response("describe_security_group_rules", rules_response([]))
    st_ddb.add_response("transact_write_items", {})
    st_sched.add_client_error("create_schedule", service_error_code="InternalServerException",
                              service_message="Mock schedule failure")
    st_ddb.add_response("update_item", {"Attributes": ddb_item({"pk": "CHG#x", "status": "FAILED"})})  # T8
    st_ddb.add_response("delete_item", {})  # lock release

    resp = apply_handler({"body": json.dumps(apply_payload())}, None)
    body = json.loads(resp.get("body", "{}"))

    assert resp["statusCode"] == 502, resp
    assert body["error"]["code"] == "SCHEDULE_FAILED"
    assert body["error"]["change_id"]
    assert body["error"]["status"] == "FAILED"

    upd = calls(recorded, "update_item")
    assert len(upd) == 1
    assert upd[0]["TableName"] == TABLE
    assert upd[0]["UpdateExpression"].startswith("SET #status = :F")
    assert upd[0]["ExpressionAttributeNames"]["#status"] == "status"

    lock_dels = calls(recorded, "delete_item")
    assert len(lock_dels) == 1
    assert lock_dels[0]["Key"]["pk"]["S"].startswith("SGLOCK#")

    assert len(calls(recorded, "create_schedule")) == 1  # attempted, then failed
    assert calls(recorded, "authorize_security_group_ingress") == []

    st_ec2.assert_no_pending_responses()
    st_ddb.assert_no_pending_responses()
    st_sched.assert_no_pending_responses()