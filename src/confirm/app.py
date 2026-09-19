"""
Deadman Confirm Lambda Handler.
Spec §2, §4, §5.
"""
import os
import json
import time
from typing import Dict, Any

from common.aws import get_dynamodb_client, get_scheduler_client
from common.errors import DeadmanError, make_error_response
from common.ddb import get_change, t2_confirm, release_sg_lock


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    table_name = os.environ.get("TABLE_NAME", "deadman-changes-shared")
    schedule_group = os.environ.get("SCHEDULE_GROUP", "deadman-shared")

    # Change ID from path parameters
    path_params = event.get("pathParameters") or {}
    change_id = path_params.get("id") or path_params.get("change_id")

    if not change_id:
        return make_error_response("INVALID_REQUEST", "Missing change id in request path", status_code=400)

    ddb_client = get_dynamodb_client()
    scheduler_client = get_scheduler_client()
    now_ts = int(time.time())

    try:
        # Perform T2 transition
        updated_item, is_idempotent = t2_confirm(
            ddb_client=ddb_client,
            table_name=table_name,
            change_id=change_id,
            now_ts=now_ts,
        )

        sg_id = updated_item.get("sg_id", "")
        schedule_name = updated_item.get("schedule_name") or f"dm-{change_id}"

        # Clean up schedule and lock if freshly confirmed
        if not is_idempotent:
            try:
                scheduler_client.delete_schedule(
                    Name=schedule_name,
                    GroupName=schedule_group,
                )
            except Exception:
                # ResourceNotFound or already deleted is expected & OK
                pass

            if sg_id:
                release_sg_lock(ddb_client, table_name, sg_id, change_id)

        confirmed_at = updated_item.get("confirmed_at") or now_ts

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "change_id": change_id,
                "status": "CONFIRMED",
                "confirmed_at": confirmed_at,
                "idempotent": is_idempotent,
            }),
        }

    except DeadmanError as de:
        return de.to_response()
    except Exception as e:
        return make_error_response("INTERNAL", f"Confirm failed: {e}", change_id=change_id, status_code=500)
