"""
Deadman Apply Lambda Handler.
Spec §2, §3, §4, §5.
"""
import os
import json
import time
import datetime
import uuid
from typing import Dict, Any, List

import botocore.exceptions

from common.aws import get_ec2_client, get_dynamodb_client, get_scheduler_client
from common.errors import DeadmanError, make_error_response
from common.models import validate_create_payload
from common.rules import describe_sg_rules, plan_delta, apply_cut_ops, reconcile_revert_ops
from common.states import (
    STATUS_PENDING,
    STATUS_REVERTED,
    TRIGGER_APPLY_FAILURE,
)
from common.ddb import (
    transact_put_change_and_lock,
    release_sg_lock,
    t1_fence,
    t1b_cut_done,
    t3_start_revert,
    finish_revert,
    t8_schedule_failed,
)


def generate_change_id() -> str:
    """Generate ULID-like 26-char Crockford Base32 identifier."""
    # Millisecond timestamp (48-bit) + 80-bit random
    t_ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)
    b = t_ms.to_bytes(6, byteorder="big") + rand_bytes
    # Crockford's Base32 alphabet
    crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    num = int.from_bytes(b, byteorder="big")
    chars = []
    for _ in range(26):
        chars.append(crockford[num & 31])
        num >>= 5
    return "".join(reversed(chars))


def format_at_schedule(expires_at: int) -> str:
    """Format UTC timestamp for EventBridge Scheduler at() expression."""
    utc_dt = datetime.datetime.fromtimestamp(expires_at, tz=datetime.timezone.utc)
    return utc_dt.strftime("at(%Y-%m-%dT%H:%M:%S)")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    table_name = os.environ.get("TABLE_NAME", "deadman-changes-shared")
    schedule_group = os.environ.get("SCHEDULE_GROUP", "deadman-shared")
    revert_fn_arn = os.environ.get("REVERT_FN_ARN", "")
    scheduler_role_arn = os.environ.get("SCHEDULER_ROLE_ARN", "")
    managed_tag_key = os.environ.get("MANAGED_TAG_KEY", "deadman:managed")
    managed_tag_val = os.environ.get("MANAGED_TAG_VALUE", "true")
    stage_tag_key = os.environ.get("STAGE_TAG_KEY", "deadman:stage")
    stage = os.environ.get("STAGE", "shared")
    min_ttl = int(os.environ.get("MIN_TTL_SECONDS", "60"))
    max_ttl = int(os.environ.get("MAX_TTL_SECONDS", "600"))
    apply_lease_sec = int(os.environ.get("APPLY_LEASE_SECONDS", "20"))

    # Parse body
    body = event.get("body")
    if isinstance(body, str):
        try:
            payload = json.loads(body)
        except Exception:
            return make_error_response("INVALID_REQUEST", "Malformed JSON body", status_code=400)
    elif isinstance(body, dict):
        payload = body
    else:
        payload = {}

    ec2_client = get_ec2_client()
    ddb_client = get_dynamodb_client()
    scheduler_client = get_scheduler_client()

    change_id: str = ""
    sg_id: str = ""

    try:
        # Step 1: Validate request schema & limits
        validate_create_payload(payload, min_ttl=min_ttl, max_ttl=max_ttl)
        sg_id = payload["sg_id"]
        ttl_seconds = payload["ttl_seconds"]
        requested_ops = payload["ops"]

        # Step 2: Check SG exists and has required management tags
        try:
            sg_resp = ec2_client.describe_security_groups(GroupIds=[sg_id])
            sgs = sg_resp.get("SecurityGroups", [])
            if not sgs:
                raise DeadmanError("SG_NOT_FOUND", f"Security group {sg_id} not found", status_code=404)
            sg_tags = {t.get("Key"): t.get("Value") for t in sgs[0].get("Tags", [])}
        except botocore.exceptions.ClientError as ce:
            err_code = ce.response.get("Error", {}).get("Code", "")
            if "NotFound" in err_code or "InvalidGroup" in err_code:
                raise DeadmanError("SG_NOT_FOUND", f"Security group {sg_id} not found", status_code=404)
            raise ce

        if sg_tags.get(managed_tag_key) != managed_tag_val or sg_tags.get(stage_tag_key) != stage:
            raise DeadmanError(
                "SG_NOT_MANAGED",
                f"Security group {sg_id} lacks required tags: {managed_tag_key}={managed_tag_val} and {stage_tag_key}={stage}",
                status_code=403,
            )

        # Step 3: Plan delta from a fresh describe
        snapshot_rules = describe_sg_rules(ec2_client, sg_id)
        delta = plan_delta(snapshot_rules, requested_ops)

        # Step 4: Generate IDs and timestamps
        change_id = generate_change_id()
        now_ts = int(time.time())
        expires_at = now_ts + ttl_seconds
        lock_until = expires_at + 360
        purge_at = expires_at + 604800
        schedule_name = f"dm-{change_id}"

        change_data = {
            "change_id": change_id,
            "status": STATUS_PENDING,
            "sg_id": sg_id,
            "ttl_seconds": ttl_seconds,
            "created_at": now_ts,
            "expires_at": expires_at,
            "schedule_name": schedule_name,
            "apply_done": False,
            "snapshot": snapshot_rules,
            "delta": delta,
            "confirmed_at": None,
            "reverted_at": None,
            "revert_trigger": None,
            "revert_report": None,
            "failure_reason": None,
            "purge_at": purge_at,
        }

        # Step 5: T0 - TransactWrite (CHG + SGLOCK)
        transact_put_change_and_lock(
            ddb_client=ddb_client,
            table_name=table_name,
            change_data=change_data,
            sg_id=sg_id,
            lock_until=lock_until,
            now_ts=now_ts,
        )

    except DeadmanError as de:
        return de.to_response()
    except Exception as e:
        return make_error_response("INTERNAL", f"Apply initialization failed: {e}", status_code=500)

    # Step 6: Create EventBridge schedule (Arming)
    schedule_created = False
    schedule_expr = format_at_schedule(expires_at)
    target_input = json.dumps({"trigger": "SCHEDULE", "change_id": change_id})

    try:
        scheduler_client.create_schedule(
            Name=schedule_name,
            GroupName=schedule_group,
            ScheduleExpression=schedule_expr,
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Target={
                "Arn": revert_fn_arn,
                "RoleArn": scheduler_role_arn,
                "Input": target_input,
            },
        )
        schedule_created = True
    except botocore.exceptions.ClientError as ce:
        code = ce.response.get("Error", {}).get("Code", "")
        if code == "ConflictException":
            # Schedule already exists with this name; treat as armed per spec
            schedule_created = True
        else:
            # T8 failure path: nothing applied
            try:
                t8_schedule_failed(ddb_client, table_name, change_id, f"Schedule creation failed: {ce}")
                release_sg_lock(ddb_client, table_name, sg_id, change_id)
            except Exception:
                pass
            return make_error_response(
                "SCHEDULE_FAILED",
                f"Failed to create schedule: {ce}",
                change_id=change_id,
                status="FAILED",
                status_code=502,
            )
    except Exception as e:
        try:
            t8_schedule_failed(ddb_client, table_name, change_id, f"Schedule creation failed: {e}")
            release_sg_lock(ddb_client, table_name, sg_id, change_id)
        except Exception:
            pass
        return make_error_response(
            "SCHEDULE_FAILED",
            f"Failed to create schedule: {e}",
            change_id=change_id,
            status="FAILED",
            status_code=502,
        )

    # Step 7: T1 fence before cutting ops
    try:
        t1_fence(ddb_client, table_name, change_id, now_ts=int(time.time()), lease_seconds=apply_lease_sec)
    except DeadmanError as de:
        # Revert might have won or status changed
        return de.to_response()
    except Exception as e:
        return make_error_response("INTERNAL", f"T1 fence failed: {e}", change_id=change_id, status_code=500)

    # Step 8: Cut ops
    cut_success = False
    updated_delta = delta
    try:
        updated_delta = apply_cut_ops(ec2_client, sg_id, delta)
        cut_success = True
    except Exception as apply_err:
        # Failure path after arming: run T3 with APPLY_FAILURE, reconcile, best-effort DeleteSchedule
        try:
            owner_id = f"apply_failure_{change_id}"
            t3_start_revert(
                ddb_client=ddb_client,
                table_name=table_name,
                change_id=change_id,
                owner_id=owner_id,
                trigger=TRIGGER_APPLY_FAILURE,
                now_ts=int(time.time()),
            )
            # Reconcile applied ops
            final_status, revert_report = reconcile_revert_ops(ec2_client, sg_id, updated_delta)
            finish_revert(
                ddb_client=ddb_client,
                table_name=table_name,
                change_id=change_id,
                owner_id=owner_id,
                target_status=final_status,
                revert_report=revert_report,
                now_ts=int(time.time()),
                failure_reason=f"Apply cut failed: {apply_err}",
            )
            release_sg_lock(ddb_client, table_name, sg_id, change_id)
            # Best-effort DeleteSchedule
            try:
                scheduler_client.delete_schedule(Name=schedule_name, GroupName=schedule_group)
            except Exception:
                pass

            if final_status == STATUS_REVERTED:
                return make_error_response(
                    "APPLY_FAILED_REVERTED",
                    f"Apply failed during cut; all changes reverted: {apply_err}",
                    change_id=change_id,
                    status=final_status,
                    status_code=500,
                )
            else:
                return make_error_response(
                    "APPLY_FAILED_REVERT_INCOMPLETE",
                    f"Apply failed during cut and revert incomplete ({final_status}): {apply_err}",
                    change_id=change_id,
                    status=final_status,
                    status_code=500,
                )
        except Exception as recovery_err:
            return make_error_response(
                "APPLY_FAILED_REVERT_INCOMPLETE",
                f"Apply cut failed ({apply_err}) and recovery failed ({recovery_err})",
                change_id=change_id,
                status="FAILED",
                status_code=500,
            )

    # Step 9: T1b - cut done
    try:
        t1b_cut_done(ddb_client, table_name, change_id, updated_delta)
    except DeadmanError as de:
        return de.to_response()
    except Exception as e:
        return make_error_response("INTERNAL", f"T1b mark cut done failed: {e}", change_id=change_id, status_code=500)

    # Step 10: 201 Created Response
    response_body = {
        "change_id": change_id,
        "status": STATUS_PENDING,
        "sg_id": sg_id,
        "ttl_seconds": ttl_seconds,
        "created_at": now_ts,
        "expires_at": expires_at,
        "server_time": int(time.time()),
        "schedule_name": schedule_name,
        "delta": updated_delta,
    }

    return {
        "statusCode": 201,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(response_body),
    }
