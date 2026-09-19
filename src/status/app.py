"""
Deadman Status Lambda Handler.
Spec §2, §5.
"""
import os
import json
import time
from typing import Dict, Any

from common.aws import get_dynamodb_client
from common.errors import DeadmanError, make_error_response
from common.ddb import get_change


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    table_name = os.environ.get("TABLE_NAME", "deadman-changes-shared")

    path_params = event.get("pathParameters") or {}
    change_id = path_params.get("id") or path_params.get("change_id")

    if not change_id:
        return make_error_response("INVALID_REQUEST", "Missing change id in request path", status_code=400)

    ddb_client = get_dynamodb_client()
    server_time = int(time.time())

    try:
        item = get_change(ddb_client, table_name, change_id)
        response_body = {
            "change_id": item.get("change_id", change_id),
            "status": item.get("status"),
            "sg_id": item.get("sg_id"),
            "ttl_seconds": item.get("ttl_seconds"),
            "created_at": item.get("created_at"),
            "expires_at": item.get("expires_at"),
            "server_time": server_time,
            "apply_done": item.get("apply_done", False),
            "delta": item.get("delta", []),
            "confirmed_at": item.get("confirmed_at"),
            "reverted_at": item.get("reverted_at"),
            "revert_trigger": item.get("revert_trigger"),
            "revert_report": item.get("revert_report"),
            "failure_reason": item.get("failure_reason"),
        }

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(response_body),
        }
    except DeadmanError as de:
        return de.to_response()
    except Exception as e:
        return make_error_response("INTERNAL", f"Status fetch failed: {e}", change_id=change_id, status_code=500)
