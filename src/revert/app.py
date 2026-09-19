"""
Deadman Revert Lambda Handler.
Spec §2, §4, §5.
Dispatches both Scheduled invocations and Manual API invocations.
"""
import os
import json
import time
import uuid
from typing import Dict, Any, Tuple

import botocore.exceptions

from common.aws import get_ec2_client, get_dynamodb_client, get_scheduler_client
from common.errors import DeadmanError, make_error_response
from common.rules import describe_sg_rules, reconcile_revert_ops
from common.states import (
    STATUS_PENDING,
    STATUS_CONFIRMED,
    STATUS_REVERTING,
    STATUS_REVERTED,
    STATUS_PARTIAL_REVERT,
    STATUS_FAILED,
    TRIGGER_SCHEDULE,
    TRIGGER_MANUAL,
    RESULT_ERROR,
)
from common.ddb import (
    get_change,
    t3_start_revert,
    finish_revert,
    release_sg_lock,
)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    table_name = os.environ.get("TABLE_NAME", "deadman-changes-shared")
    schedule_group = os.environ.get("SCHEDULE_GROUP", "deadman-shared")
    managed_tag_key = os.environ.get("MANAGED_TAG_KEY", "deadman:managed")
    managed_tag_val = os.environ.get("MANAGED_TAG_VALUE", "true")
    stage_tag_key = os.environ.get("STAGE_TAG_KEY", "deadman:stage")
    stage = os.environ.get("STAGE", "shared")
    revert_lease_sec = int(os.environ.get("REVERT_LEASE_SECONDS", "60"))

    # Dispatch logic per Spec §2:
    # trigger == "SCHEDULE" -> scheduled path
    # requestContext.http present -> manual path
    # anything else -> error
    is_scheduled = (event.get("trigger") == TRIGGER_SCHEDULE)
    is_manual = bool(event.get("requestContext", {}).get("http"))

    if not is_scheduled and not is_manual:
        # Fallback check: stringified JSON in event or custom test payload
        if isinstance(event.get("body"), str):
            try:
                b = json.loads(event["body"])
                if b.get("trigger") == TRIGGER_SCHEDULE:
                    is_scheduled = True
            except Exception:
                pass

    if not is_scheduled and not is_manual:
        return make_error_response("INVALID_REQUEST", "Unrecognized invocation dispatch context", status_code=400)

    if is_scheduled:
        change_id = event.get("change_id")
        trigger = TRIGGER_SCHEDULE
    else:
        path_params = event.get("pathParameters") or {}
        change_id = path_params.get("id") or path_params.get("change_id")
        trigger = TRIGGER_MANUAL

    if not change_id:
        return make_error_response("INVALID_REQUEST", "Missing change_id", status_code=400)

    ddb_client = get_dynamodb_client()
    ec2_client = get_ec2_client()
    scheduler_client = get_scheduler_client()

    owner_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    now_ts = int(time.time())

    # Step 1: Attempt T3 (or T4 takeover if expired)
    try:
        updated_item, is_idempotent = t3_start_revert(
            ddb_client=ddb_client,
            table_name=table_name,
            change_id=change_id,
            owner_id=owner_id,
            trigger=trigger,
            now_ts=now_ts,
            lease_seconds=revert_lease_sec,
        )
    except DeadmanError as de:
        if is_scheduled:
            # Scheduled revert after confirm or terminal: log NOOP and return 200 OK
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"status": "NOOP", "reason": de.code, "message": de.message}),
            }
        return de.to_response()
    except Exception as e:
        if is_scheduled:
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"status": "ERROR", "message": str(e)}),
            }
        return make_error_response("INTERNAL", f"Failed to initiate revert: {e}", change_id=change_id, status_code=500)

    sg_id = updated_item.get("sg_id", "")
    delta = updated_item.get("delta", [])
    schedule_name = updated_item.get("schedule_name") or f"dm-{change_id}"

    # If idempotent (already REVERTED)
    if is_idempotent:
        if is_scheduled:
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"status": "NOOP", "change_id": change_id, "message": "Already reverted"}),
            }
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "change_id": change_id,
                "status": STATUS_REVERTED,
                "revert_trigger": updated_item.get("revert_trigger") or trigger,
                "reverted_at": updated_item.get("reverted_at") or now_ts,
                "idempotent": True,
                "revert_report": updated_item.get("revert_report") or [],
            }),
        }

    # Manual path: best-effort DeleteSchedule
    if is_manual:
        try:
            scheduler_client.delete_schedule(
                Name=schedule_name,
                GroupName=schedule_group,
            )
        except Exception:
            pass

    # Step 2: Precondition check (SG existence and management tags)
    precondition_failed = False
    failure_reason = ""
    try:
        sg_resp = ec2_client.describe_security_groups(GroupIds=[sg_id])
        sgs = sg_resp.get("SecurityGroups", [])
        if not sgs:
            precondition_failed = True
            failure_reason = f"Security group {sg_id} not found"
        else:
            sg_tags = {t.get("Key"): t.get("Value") for t in sgs[0].get("Tags", [])}
            if sg_tags.get(managed_tag_key) != managed_tag_val or sg_tags.get(stage_tag_key) != stage:
                precondition_failed = True
                failure_reason = f"Security group {sg_id} tag check failed or tag removed"
    except botocore.exceptions.ClientError as ce:
        precondition_failed = True
        failure_reason = f"Precondition error describing SG {sg_id}: {ce}"
    except Exception as e:
        precondition_failed = True
        failure_reason = f"Precondition error: {e}"

    if precondition_failed:
        # T7: Precondition error, nothing done -> FAILED
        revert_report = [
            {"op_id": op.get("op_id", i), "result": RESULT_ERROR, "detail": failure_reason}
            for i, op in enumerate(delta, start=1)
        ]
        try:
            finish_revert(
                ddb_client=ddb_client,
                table_name=table_name,
                change_id=change_id,
                owner_id=owner_id,
                target_status=STATUS_FAILED,
                revert_report=revert_report,
                now_ts=int(time.time()),
                failure_reason=failure_reason,
            )
            release_sg_lock(ddb_client, table_name, sg_id, change_id)
        except Exception:
            pass

        if is_scheduled:
            return {"statusCode": 200, "body": json.dumps({"status": STATUS_FAILED, "reason": failure_reason})}

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "change_id": change_id,
                "status": STATUS_FAILED,
                "revert_trigger": trigger,
                "reverted_at": int(time.time()),
                "idempotent": False,
                "revert_report": revert_report,
            }),
        }

    # Step 3: Per-op reconcile
    try:
        final_status, revert_report = reconcile_revert_ops(ec2_client, sg_id, delta)
    except Exception as e:
        # Fallback: never raise, record outcome
        final_status = STATUS_FAILED
        revert_report = [
            {"op_id": op.get("op_id", i), "result": RESULT_ERROR, "detail": str(e)}
            for i, op in enumerate(delta, start=1)
        ]

    # Step 4: T5/T6/T7 - Update state in DynamoDB & release lock
    reverted_at = int(time.time())
    try:
        finish_revert(
            ddb_client=ddb_client,
            table_name=table_name,
            change_id=change_id,
            owner_id=owner_id,
            target_status=final_status,
            revert_report=revert_report,
            now_ts=reverted_at,
        )
    except Exception as e:
        # Fallback if update fails
        pass

    release_sg_lock(ddb_client, table_name, sg_id, change_id)

    # Step 5: Return response
    if is_scheduled:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "change_id": change_id,
                "status": final_status,
                "reverted_at": reverted_at,
            }),
        }

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "change_id": change_id,
            "status": final_status,
            "revert_trigger": trigger,
            "reverted_at": reverted_at,
            "idempotent": False,
            "revert_report": revert_report,
        }),
    }
