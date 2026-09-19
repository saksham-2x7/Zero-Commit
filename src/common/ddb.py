"""
DynamoDB data access layer implementing state machine transitions T0 through T8.
Spec §3 DynamoDB Schema, Spec §4 State Machine.
"""
from typing import Dict, Any, Optional, Tuple
import time
import boto3
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
import botocore.exceptions

from common.errors import DeadmanError
from common.states import (
    STATUS_PENDING,
    STATUS_CONFIRMED,
    STATUS_REVERTING,
    STATUS_REVERTED,
    STATUS_PARTIAL_REVERT,
    STATUS_FAILED,
    COND_T0_CHG,
    COND_T0_SGLOCK,
    COND_T1_FENCE,
    COND_T1B_CUT_DONE,
    COND_T2_CONFIRM,
    COND_T3_REVERT,
    COND_T4_TAKEOVER,
    COND_T5_REVERTED,
    COND_T8_SCHEDULE_FAILED,
)

import decimal

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def unwrap_item(val: Any) -> Any:
    if isinstance(val, dict):
        return {k: unwrap_item(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [unwrap_item(v) for v in val]
    elif isinstance(val, decimal.Decimal):
        if val % 1 == 0:
            return int(val)
        return float(val)
    return val


def python_to_dynamo(val: Any) -> Dict[str, Any]:
    return _serializer.serialize(val)


def dynamo_to_python(dynamo_dict: Dict[str, Any]) -> Dict[str, Any]:
    raw = {k: _deserializer.deserialize(v) for k, v in dynamo_dict.items()}
    return unwrap_item(raw)


def get_change(ddb_client: Any, table_name: str, change_id: str) -> Dict[str, Any]:
    """Retrieve change item by ID."""
    pk = f"CHG#{change_id}"
    resp = ddb_client.get_item(
        TableName=table_name,
        Key={"pk": {"S": pk}},
        ConsistentRead=True
    )
    item = resp.get("Item")
    if not item:
        raise DeadmanError("CHANGE_NOT_FOUND", f"Change {change_id} not found", change_id=change_id, status_code=404)
    return dynamo_to_python(item)


def transact_put_change_and_lock(
    ddb_client: Any,
    table_name: str,
    change_data: Dict[str, Any],
    sg_id: str,
    lock_until: int,
    now_ts: int,
) -> None:
    """
    T0: TransactWriteItems (Put CHG + Put SGLOCK).
    CHG: attribute_not_exists(pk)
    SGLOCK: attribute_not_exists(pk) OR lock_until < :now
    """
    chg_pk = f"CHG#{change_data['change_id']}"
    lock_pk = f"SGLOCK#{sg_id}"

    chg_item = {k: python_to_dynamo(v) for k, v in change_data.items()}
    chg_item["pk"] = {"S": chg_pk}

    lock_item = {
        "pk": {"S": lock_pk},
        "active_change_id": {"S": change_data["change_id"]},
        "lock_until": {"N": str(lock_until)},
    }

    try:
        ddb_client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": table_name,
                        "Item": chg_item,
                        "ConditionExpression": COND_T0_CHG,
                    }
                },
                {
                    "Put": {
                        "TableName": table_name,
                        "Item": lock_item,
                        "ConditionExpression": COND_T0_SGLOCK,
                        "ExpressionAttributeValues": {
                            ":now": {"N": str(now_ts)}
                        },
                    }
                },
            ]
        )
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "TransactionCanceledException":
            cancellation_reasons = e.response.get("CancellationReasons", [])
            if len(cancellation_reasons) > 1 and cancellation_reasons[1].get("Code") == "ConditionalCheckFailed":
                raise DeadmanError(
                    "SG_BUSY",
                    f"Active change already exists on security group {sg_id}",
                    status_code=409,
                )
            if len(cancellation_reasons) > 0 and cancellation_reasons[0].get("Code") == "ConditionalCheckFailed":
                raise DeadmanError(
                    "INVALID_REQUEST",
                    f"Change ID {change_data['change_id']} already exists",
                    status_code=400,
                )
        raise e


def release_sg_lock(ddb_client: Any, table_name: str, sg_id: str, change_id: Optional[str] = None) -> None:
    """Best-effort lock release."""
    lock_pk = f"SGLOCK#{sg_id}"
    try:
        kwargs: Dict[str, Any] = {
            "TableName": table_name,
            "Key": {"pk": {"S": lock_pk}},
        }
        if change_id:
            kwargs["ConditionExpression"] = "active_change_id = :cid"
            kwargs["ExpressionAttributeValues"] = {":cid": {"S": change_id}}
        ddb_client.delete_item(**kwargs)
    except Exception:
        # Best effort per spec
        pass


def t1_fence(ddb_client: Any, table_name: str, change_id: str, now_ts: int, lease_seconds: int = 20) -> None:
    """
    T1 fence: sets apply_lease_until before cutting ops.
    Condition: status = :P AND apply_done = :false AND attribute_not_exists(apply_lease_until)
    """
    pk = f"CHG#{change_id}"
    lease_until = now_ts + lease_seconds
    try:
        ddb_client.update_item(
            TableName=table_name,
            Key={"pk": {"S": pk}},
            ConditionExpression=COND_T1_FENCE,
            UpdateExpression="SET apply_lease_until = :lease_until",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":P": {"S": STATUS_PENDING},
                ":false": {"BOOL": False},
                ":lease_until": {"N": str(lease_until)},
            },
        )
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            current = get_change(ddb_client, table_name, change_id)
            status = current.get("status")
            if status != STATUS_PENDING:
                raise DeadmanError(
                    "CHANGE_NOT_PENDING",
                    f"Change {change_id} is in status {status}",
                    change_id=change_id,
                    status=status,
                    status_code=409,
                )
            raise DeadmanError(
                "CHANGE_NOT_PENDING",
                f"Apply fence failed on change {change_id}",
                change_id=change_id,
                status=status,
                status_code=409,
            )
        raise e


def t1b_cut_done(ddb_client: Any, table_name: str, change_id: str, updated_delta: list) -> None:
    """
    T1b: after cutting ops, marks apply_done = true and stores updated delta.
    Condition: status = :P
    """
    pk = f"CHG#{change_id}"
    dynamo_delta = python_to_dynamo(updated_delta)
    try:
        ddb_client.update_item(
            TableName=table_name,
            Key={"pk": {"S": pk}},
            ConditionExpression=COND_T1B_CUT_DONE,
            UpdateExpression="SET apply_done = :true, delta = :delta",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":P": {"S": STATUS_PENDING},
                ":true": {"BOOL": True},
                ":delta": dynamo_delta,
            },
        )
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            current = get_change(ddb_client, table_name, change_id)
            raise DeadmanError(
                "CHANGE_NOT_PENDING",
                f"Change {change_id} is in status {current.get('status')}",
                change_id=change_id,
                status=current.get("status"),
                status_code=409,
            )
        raise e


def t2_confirm(ddb_client: Any, table_name: str, change_id: str, now_ts: int) -> Tuple[Dict[str, Any], bool]:
    """
    T2: PENDING -> CONFIRMED.
    Condition: status = :P AND apply_done = :true AND expires_at > :now
    Returns (item, idempotent_flag).
    """
    pk = f"CHG#{change_id}"
    try:
        resp = ddb_client.update_item(
            TableName=table_name,
            Key={"pk": {"S": pk}},
            ConditionExpression=COND_T2_CONFIRM,
            UpdateExpression="SET #status = :C, confirmed_at = :now",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":P": {"S": STATUS_PENDING},
                ":C": {"S": STATUS_CONFIRMED},
                ":true": {"BOOL": True},
                ":now": {"N": str(now_ts)},
            },
            ReturnValues="ALL_NEW",
        )
        return dynamo_to_python(resp["Attributes"]), False
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            current = get_change(ddb_client, table_name, change_id)
            curr_status = current.get("status")
            if curr_status == STATUS_CONFIRMED:
                return current, True
            if curr_status == STATUS_PENDING:
                if not current.get("apply_done"):
                    raise DeadmanError(
                        "NOT_APPLIED_YET",
                        f"Change {change_id} apply is still in flight",
                        change_id=change_id,
                        status=curr_status,
                        status_code=409,
                    )
                if current.get("expires_at", 0) <= now_ts:
                    raise DeadmanError(
                        "WINDOW_EXPIRED",
                        f"Confirmation window for change {change_id} has expired",
                        change_id=change_id,
                        status=curr_status,
                        status_code=409,
                    )
            raise DeadmanError(
                "CHANGE_NOT_PENDING",
                f"Change {change_id} is not PENDING (current status: {curr_status})",
                change_id=change_id,
                status=curr_status,
                status_code=409,
            )
        raise e


def t3_start_revert(
    ddb_client: Any,
    table_name: str,
    change_id: str,
    owner_id: str,
    trigger: str,
    now_ts: int,
    lease_seconds: int = 60,
) -> Tuple[Dict[str, Any], bool]:
    """
    T3: PENDING -> REVERTING.
    Condition: status = :P AND (apply_done = :true OR attribute_not_exists(apply_lease_until) OR apply_lease_until < :now)
    (For APPLY_FAILURE, caller is apply Lambda aborting its own active lease: status = :P)
    Returns (item, idempotent_flag).
    """
    from common.states import TRIGGER_APPLY_FAILURE
    pk = f"CHG#{change_id}"
    lease_until = now_ts + lease_seconds
    cond_expr = "#status = :P" if trigger == TRIGGER_APPLY_FAILURE else COND_T3_REVERT
    attr_values = {
        ":P": {"S": STATUS_PENDING},
        ":R": {"S": STATUS_REVERTING},
        ":me": {"S": owner_id},
        ":lease_until": {"N": str(lease_until)},
        ":trigger": {"S": trigger},
    }
    if trigger != TRIGGER_APPLY_FAILURE:
        attr_values[":true"] = {"BOOL": True}
        attr_values[":now"] = {"N": str(now_ts)}

    try:
        resp = ddb_client.update_item(
            TableName=table_name,
            Key={"pk": {"S": pk}},
            ConditionExpression=cond_expr,
            UpdateExpression="SET #status = :R, revert_owner = :me, revert_lease_until = :lease_until, revert_trigger = :trigger",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=attr_values,
            ReturnValues="ALL_NEW",
        )
        return dynamo_to_python(resp["Attributes"]), False
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            current = get_change(ddb_client, table_name, change_id)
            curr_status = current.get("status")
            if curr_status == STATUS_REVERTED:
                return current, True
            if curr_status == STATUS_REVERTING:
                # Check lease
                curr_lease = current.get("revert_lease_until", 0)
                if curr_lease >= now_ts:
                    raise DeadmanError(
                        "REVERT_IN_PROGRESS",
                        f"Revert is already in progress for change {change_id}",
                        change_id=change_id,
                        status=curr_status,
                        status_code=409,
                    )
                # Expired lease: attempt T4 takeover
                return t4_takeover(ddb_client, table_name, change_id, owner_id, now_ts, lease_seconds)
            if curr_status == STATUS_PENDING:
                # Apply lease is currently active
                raise DeadmanError(
                    "NOT_APPLIED_YET",
                    f"Change {change_id} apply lease is currently active",
                    change_id=change_id,
                    status=curr_status,
                    status_code=409,
                )
            raise DeadmanError(
                "CHANGE_NOT_PENDING",
                f"Change {change_id} is in status {curr_status}",
                change_id=change_id,
                status=curr_status,
                status_code=409,
            )
        raise e


def t4_takeover(
    ddb_client: Any,
    table_name: str,
    change_id: str,
    owner_id: str,
    now_ts: int,
    lease_seconds: int = 60,
) -> Tuple[Dict[str, Any], bool]:
    """
    T4: REVERTING -> REVERTING (takeover expired lease).
    Condition: status = :R AND revert_lease_until < :now
    """
    pk = f"CHG#{change_id}"
    lease_until = now_ts + lease_seconds
    try:
        resp = ddb_client.update_item(
            TableName=table_name,
            Key={"pk": {"S": pk}},
            ConditionExpression=COND_T4_TAKEOVER,
            UpdateExpression="SET revert_owner = :me, revert_lease_until = :lease_until",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":R": {"S": STATUS_REVERTING},
                ":now": {"N": str(now_ts)},
                ":me": {"S": owner_id},
                ":lease_until": {"N": str(lease_until)},
            },
            ReturnValues="ALL_NEW",
        )
        return dynamo_to_python(resp["Attributes"]), False
    except botocore.exceptions.ClientError as e:
        current = get_change(ddb_client, table_name, change_id)
        raise DeadmanError(
            "REVERT_IN_PROGRESS",
            f"Takeover failed for change {change_id}",
            change_id=change_id,
            status=current.get("status"),
            status_code=409,
        )


def finish_revert(
    ddb_client: Any,
    table_name: str,
    change_id: str,
    owner_id: str,
    target_status: str,
    revert_report: list,
    now_ts: int,
    failure_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    T5/T6/T7: Finish revert and set REVERTED / PARTIAL_REVERT / FAILED.
    Condition: status = :R AND revert_owner = :me
    """
    pk = f"CHG#{change_id}"
    dynamo_report = python_to_dynamo(revert_report)

    update_expr = "SET #status = :target, reverted_at = :now, revert_report = :report"
    attr_values: Dict[str, Any] = {
        ":R": {"S": STATUS_REVERTING},
        ":me": {"S": owner_id},
        ":target": {"S": target_status},
        ":now": {"N": str(now_ts)},
        ":report": dynamo_report,
    }

    if failure_reason:
        update_expr += ", failure_reason = :reason"
        attr_values[":reason"] = {"S": failure_reason}

    resp = ddb_client.update_item(
        TableName=table_name,
        Key={"pk": {"S": pk}},
        ConditionExpression=COND_T5_REVERTED,
        UpdateExpression=update_expr,
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues=attr_values,
        ReturnValues="ALL_NEW",
    )
    return dynamo_to_python(resp["Attributes"])


def t8_schedule_failed(
    ddb_client: Any,
    table_name: str,
    change_id: str,
    failure_reason: str,
) -> Dict[str, Any]:
    """
    T8: PENDING -> FAILED (CreateSchedule failed, nothing cut).
    Condition: status = :P AND apply_done = :false
    """
    pk = f"CHG#{change_id}"
    resp = ddb_client.update_item(
        TableName=table_name,
        Key={"pk": {"S": pk}},
        ConditionExpression=COND_T8_SCHEDULE_FAILED,
        UpdateExpression="SET #status = :F, failure_reason = :reason",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":P": {"S": STATUS_PENDING},
            ":F": {"S": STATUS_FAILED},
            ":reason": {"S": failure_reason},
        },
        ReturnValues="ALL_NEW",
    )
    return dynamo_to_python(resp["Attributes"])
