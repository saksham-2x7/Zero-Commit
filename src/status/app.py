import json
from common.ddb import DecimalEncoder
import time
from common.errors import get_error, DeadmanError
from common.ddb import get_table

def handler(event, context):
    try:
        change_id = event.get("pathParameters", {}).get("id")
        if not change_id:
            raise get_error("INVALID_REQUEST", "Missing change_id")
            
        table = get_table()
        res = table.get_item(Key={"pk": f"CHG#{change_id}"})
        item = res.get("Item")
        if not item:
            raise get_error("CHANGE_NOT_FOUND", "Change not found")
            
        now = int(time.time())
        
        # Format response
        resp = {
            "change_id": change_id,
            "status": item.get("status"),
            "sg_id": item.get("sg_id"),
            "ttl_seconds": int(item.get("ttl_seconds")),
            "created_at": int(item.get("created_at")),
            "expires_at": int(item.get("expires_at")),
            "server_time": now,
            "apply_done": item.get("apply_done", False),
            "delta": item.get("delta", []),
            "confirmed_at": int(item["confirmed_at"]) if item.get("confirmed_at") else None,
            "reverted_at": int(item["reverted_at"]) if item.get("reverted_at") else None,
            "revert_trigger": item.get("revert_trigger"),
            "revert_report": item.get("revert_report"),
            "failure_reason": item.get("failure_reason")
        }
        
        return {
            "statusCode": 200,
            "body": json.dumps(resp, cls=DecimalEncoder)
        }
    except DeadmanError as e:
        return {"statusCode": e.http_status, "body": json.dumps(e.to_dict(), cls=DecimalEncoder)}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": {"code": "INTERNAL", "message": str(e)}})}
