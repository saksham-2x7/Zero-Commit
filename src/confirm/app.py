import os
import json
from common.ddb import DecimalEncoder
import time
import boto3
from botocore.exceptions import ClientError

from common.errors import get_error, DeadmanError
from common.states import T2_COND, STATUS_CONFIRMED, STATUS_PENDING
from common.aws import get_scheduler_client
from common.ddb import get_table

def handler(event, context):
    try:
        change_id = event.get("pathParameters", {}).get("id")
        if not change_id:
            raise get_error("INVALID_REQUEST", "Missing change_id")
            
        now = int(time.time())
        table = get_table()
        
        # Check current status for idempotency or WINDOW_EXPIRED
        res = table.get_item(Key={"pk": f"CHG#{change_id}"})
        item = res.get("Item")
        if not item:
            raise get_error("CHANGE_NOT_FOUND", "Change not found")
            
        if item.get("status") == STATUS_CONFIRMED:
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "change_id": change_id,
                    "status": STATUS_CONFIRMED,
                    "confirmed_at": int(item.get("confirmed_at", now)),
                    "idempotent": True
                })
            }
            
        if item.get("status") != STATUS_PENDING:
            raise get_error("CHANGE_NOT_PENDING", f"Change is {item.get('status')}", change_id, item.get("status"))
            
        if not item.get("apply_done"):
            raise get_error("NOT_APPLIED_YET", "Apply is in flight", change_id, STATUS_PENDING)
            
        if now >= item.get("expires_at"):
            raise get_error("WINDOW_EXPIRED", "Late confirm", change_id, STATUS_PENDING)
            
        # T2
        try:
            table.update_item(
                Key={"pk": f"CHG#{change_id}"},
                UpdateExpression="SET #st = :c, confirmed_at = :now",
                ConditionExpression=T2_COND,
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":P": STATUS_PENDING,
                    ":c": STATUS_CONFIRMED,
                    ":true": True,
                    ":now": now
                }
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                res = table.get_item(Key={"pk": f"CHG#{change_id}"})
                cur_st = res.get("Item", {}).get("status") if res.get("Item") else None
                if cur_st == STATUS_CONFIRMED:
                    return {
                        "statusCode": 200,
                        "body": json.dumps({
                            "change_id": change_id,
                            "status": STATUS_CONFIRMED,
                            "confirmed_at": int(res["Item"].get("confirmed_at", now)),
                            "idempotent": True
                        })
                    }
                raise get_error("CHANGE_NOT_PENDING", f"Status changed to {cur_st}", change_id, cur_st)
            raise e
            
        # DeleteSchedule
        scheduler = get_scheduler_client()
        sched_group = os.environ.get("SCHEDULE_GROUP", f"deadman-{os.environ.get('STAGE', 'shared')}")
        try:
            scheduler.delete_schedule(Name=f"dm-{change_id}", GroupName=sched_group)
        except Exception:
            pass # ResourceNotFound OK
            
        # Release lock
        try:
            table.delete_item(Key={"pk": f"SGLOCK#{item['sg_id']}"})
        except:
            pass
            
        return {
            "statusCode": 200,
            "body": json.dumps({
                "change_id": change_id,
                "status": STATUS_CONFIRMED,
                "confirmed_at": now,
                "idempotent": False
            })
        }
    except DeadmanError as e:
        return {"statusCode": e.http_status, "body": json.dumps(e.to_dict(), cls=DecimalEncoder)}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": {"code": "INTERNAL", "message": str(e)}})}
