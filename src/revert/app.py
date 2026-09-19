import os
import json
from common.ddb import DecimalEncoder
import time
import boto3
from botocore.exceptions import ClientError

from common.errors import get_error, DeadmanError
from common.models import Op
from common.states import (
    T3_COND, T4_COND, T5_COND,
    STATUS_PENDING, STATUS_REVERTING, STATUS_REVERTED, STATUS_PARTIAL_REVERT, STATUS_FAILED, STATUS_CONFIRMED
)
from common.rules import reconcile_op
from common.aws import get_ec2_client, get_scheduler_client
from common.ddb import get_table

def handler(event, context):
    try:
        req_id = context.aws_request_id if hasattr(context, "aws_request_id") else "revert-lambda"
        now = int(time.time())
        table = get_table()
        
        is_manual = "requestContext" in event and "http" in event["requestContext"]
        is_sched = event.get("trigger") == "SCHEDULE"
        
        if is_manual:
            change_id = event.get("pathParameters", {}).get("id")
            trigger = "MANUAL"
        elif is_sched:
            change_id = event.get("change_id")
            trigger = "SCHEDULE"
        else:
            raise get_error("INVALID_REQUEST", "Unknown trigger")
            
        if not change_id:
            raise get_error("INVALID_REQUEST", "Missing change_id")
            
        # Get item
        res = table.get_item(Key={"pk": f"CHG#{change_id}"})
        item = res.get("Item")
        if not item:
            if is_sched: return # no-op
            raise get_error("CHANGE_NOT_FOUND", "Change not found")
            
        cur_st = item.get("status")
        
        if is_manual:
            if cur_st in (STATUS_REVERTED, STATUS_PARTIAL_REVERT, STATUS_FAILED):
                # if already terminal by manual/schedule, just return idempotent
                return {
                    "statusCode": 200,
                    "body": json.dumps({
                        "change_id": change_id,
                        "status": cur_st,
                        "revert_trigger": item.get("revert_trigger"),
                        "reverted_at": int(item.get("reverted_at", now)),
                        "idempotent": True,
                        "revert_report": item.get("revert_report")
                    }, cls=DecimalEncoder)
                }
            if cur_st == STATUS_REVERTING:
                if int(item.get("revert_lease_until", 0)) > now:
                    raise get_error("REVERT_IN_PROGRESS", "Revert in progress", change_id, STATUS_REVERTING)
            elif cur_st != STATUS_PENDING:
                raise get_error("CHANGE_NOT_PENDING", f"Change is {cur_st}", change_id, cur_st)
                
            if cur_st == STATUS_PENDING and not item.get("apply_done"):
                if "apply_lease_until" in item and int(item["apply_lease_until"]) > now:
                    raise get_error("NOT_APPLIED_YET", "Apply is in flight", change_id, STATUS_PENDING)
                    
        # T3 or T4
        try:
            if cur_st == STATUS_PENDING:
                table.update_item(
                    Key={"pk": f"CHG#{change_id}"},
                    UpdateExpression="SET #st = :r, revert_trigger = :trig, revert_owner = :me, revert_lease_until = :lu",
                    ConditionExpression=T3_COND,
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={
                        ":P": STATUS_PENDING,
                        ":true": True,
                        ":r": STATUS_REVERTING,
                        ":trig": trigger,
                        ":me": req_id,
                        ":lu": now + 60,
                        ":now": now
                    }
                )
            elif cur_st == STATUS_REVERTING:
                table.update_item(
                    Key={"pk": f"CHG#{change_id}"},
                    UpdateExpression="SET revert_owner = :me, revert_lease_until = :lu",
                    ConditionExpression=T4_COND,
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={
                        ":R": STATUS_REVERTING,
                        ":me": req_id,
                        ":lu": now + 60,
                        ":now": now
                    }
                )
            else:
                if is_sched: return # no-op
                raise get_error("CHANGE_NOT_PENDING", f"Status changed to {cur_st}", change_id, cur_st)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                if is_sched: return # no-op
                res = table.get_item(Key={"pk": f"CHG#{change_id}"})
                new_st = res.get("Item", {}).get("status") if res.get("Item") else None
                if new_st in (STATUS_REVERTED, STATUS_PARTIAL_REVERT, STATUS_FAILED):
                    return {
                        "statusCode": 200,
                        "body": json.dumps({
                            "change_id": change_id,
                            "status": new_st,
                            "revert_trigger": res["Item"].get("revert_trigger"),
                            "reverted_at": int(res["Item"].get("reverted_at", now)),
                            "idempotent": True,
                            "revert_report": res["Item"].get("revert_report")
                        }, cls=DecimalEncoder)
                    }
                elif new_st == STATUS_REVERTING:
                    raise get_error("REVERT_IN_PROGRESS", "Revert in progress", change_id, STATUS_REVERTING)
                raise get_error("CHANGE_NOT_PENDING", f"Status changed to {new_st}", change_id, new_st)
            raise e
            
        # We own the revert
        # If manual or failure path, best-effort delete schedule
        # Actually just do it best-effort for manual.
        if trigger == "MANUAL":
            scheduler = get_scheduler_client()
            sched_group = os.environ.get("SCHEDULE_GROUP", f"deadman-{os.environ.get('STAGE', 'shared')}")
            try:
                scheduler.delete_schedule(Name=f"dm-{change_id}", GroupName=sched_group)
            except:
                pass
                
        # Reconcile ops
        ops_data = item.get("delta", [])
        ops = [Op.from_dict(d) for d in ops_data]
        ops.reverse()
        
        ec2 = get_ec2_client()
        sg_id = item["sg_id"]
        
        revert_report = []
        has_error = False
        has_success = False
        precondition_error = None
        
        # Test describe to catch precondition errors (SG missing, AccessDenied)
        try:
            ec2.describe_security_group_rules(Filters=[{"Name": "group-id", "Values": [sg_id]}])
        except ClientError as e:
            precondition_error = str(e)
            
        if precondition_error:
            # T7 Precondition Error
            table.update_item(
                Key={"pk": f"CHG#{change_id}"},
                UpdateExpression="SET #st = :fail, failure_reason = :reas, reverted_at = :now",
                ConditionExpression=T5_COND,
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":R": STATUS_REVERTING,
                    ":me": req_id,
                    ":fail": STATUS_FAILED,
                    ":reas": precondition_error,
                    ":now": now
                }
            )
            # release lock
            try:
                table.delete_item(Key={"pk": f"SGLOCK#{sg_id}"})
            except: pass
            if not is_manual: return
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "change_id": change_id,
                    "status": STATUS_FAILED,
                    "revert_trigger": trigger,
                    "reverted_at": now,
                    "idempotent": False,
                    "revert_report": []
                }, cls=DecimalEncoder)
            }
            
        def authorize_fn(op):
            ip_perm = {"IpProtocol": op.protocol, "FromPort": op.from_port, "ToPort": op.to_port, "IpRanges": [{"CidrIp": op.cidr}]}
            ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[ip_perm])
            
        def revoke_fn(op):
            ip_perm = {"IpProtocol": op.protocol, "FromPort": op.from_port, "ToPort": op.to_port, "IpRanges": [{"CidrIp": op.cidr}]}
            ec2.revoke_security_group_ingress(GroupId=sg_id, IpPermissions=[ip_perm])
            
        for op in ops:
            try:
                sg_res = ec2.describe_security_groups(GroupIds=[sg_id])
                cur_rules = sg_res["SecurityGroups"][0].get("IpPermissions", [])
                result = reconcile_op(op, cur_rules, authorize_fn, revoke_fn)
                revert_report.append({"op_id": op.op_id, "result": result, "detail": ""})
                if result == "REVERTED":
                    has_success = True
            except Exception as e:
                revert_report.append({"op_id": op.op_id, "result": "ERROR", "detail": str(e)})
                has_error = True
                
        # T5 or T6
        final_st = STATUS_PARTIAL_REVERT if has_error and has_success else (STATUS_FAILED if has_error and not has_success else STATUS_REVERTED)
        # Spec says FAILED if precondition error, nothing done. If some errored and no success but some SKIPPED? Spec: "all ops REVERTED or SKIPPED -> T5 REVERTED". "some ops ERROR, some done -> T6 PARTIAL". "precondition error, nothing done -> T7 FAILED". 
        # So if ANY error -> PARTIAL_REVERT, unless all ops errored and none were REVERTED or SKIPPED? The spec says:
        # T5: all ops REVERTED or SKIPPED -> REVERTED
        # T6: some ops ERROR, some done -> PARTIAL_REVERT
        # T7: precondition error, nothing done -> FAILED
        if has_error:
            has_done = any(r["result"] in ("REVERTED", "SKIPPED_ALREADY_SATISFIED") for r in revert_report)
            if not has_done:
                final_st = STATUS_FAILED
            else:
                final_st = STATUS_PARTIAL_REVERT
        else:
            final_st = STATUS_REVERTED
            
        try:
            table.update_item(
                Key={"pk": f"CHG#{change_id}"},
                UpdateExpression="SET #st = :final, revert_report = :rep, reverted_at = :now",
                ConditionExpression=T5_COND,
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":R": STATUS_REVERTING,
                    ":me": req_id,
                    ":final": final_st,
                    ":rep": revert_report,
                    ":now": now
                }
            )
            # release lock
            try:
                table.delete_item(Key={"pk": f"SGLOCK#{sg_id}"})
            except: pass
        except ClientError as e:
            pass # Lost ownership
            
        if not is_manual:
            return
            
        return {
            "statusCode": 200,
            "body": json.dumps({
                "change_id": change_id,
                "status": final_st,
                "revert_trigger": trigger,
                "reverted_at": now,
                "idempotent": False,
                "revert_report": revert_report
            }, cls=DecimalEncoder)
        }
    except DeadmanError as e:
        if event.get("trigger") == "SCHEDULE": return
        return {"statusCode": e.http_status, "body": json.dumps(e.to_dict(), cls=DecimalEncoder)}
    except Exception as e:
        if event.get("trigger") == "SCHEDULE": return
        return {"statusCode": 500, "body": json.dumps({"error": {"code": "INTERNAL", "message": str(e)}})}
