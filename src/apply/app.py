import os
import json
from common.ddb import DecimalEncoder
import time
import ulid
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError

from common.errors import get_error, DeadmanError
from common.models import Op
from common.states import (
    T0_CHG_COND, T0_SGLOCK_COND, T1_COND, T1B_COND, T3_COND, T8_COND,
    STATUS_PENDING, STATUS_FAILED
)
from common.rules import plan_delta
from common.aws import get_ec2_client, get_scheduler_client
from common.ddb import get_table

def handler(event, context):
    try:
        body = json.loads(event.get("body", "{}"))
        sg_id = body.get("sg_id")
        ttl_seconds = body.get("ttl_seconds")
        ops_data = body.get("ops", [])
        
        if not sg_id or not ttl_seconds or not ops_data:
            raise get_error("INVALID_REQUEST", "Missing required fields")
            
        min_ttl = int(os.environ.get("MIN_TTL_SECONDS", "60"))
        max_ttl = int(os.environ.get("MAX_TTL_SECONDS", "600"))
        if not (min_ttl <= ttl_seconds <= max_ttl):
            raise get_error("TTL_OUT_OF_RANGE", f"TTL must be between {min_ttl} and {max_ttl}")
            
        if len(ops_data) > 5 or len(ops_data) == 0:
            raise get_error("INVALID_REQUEST", "Ops must be 1-5")
            
        ops = []
        for i, od in enumerate(ops_data):
            if "cidr" not in od:
                raise get_error("UNSUPPORTED_RULE", "Only IPv4 CIDR supported")
            op = Op(op_id=i+1, action=od["action"], protocol=od["protocol"], 
                    from_port=od["from_port"], to_port=od["to_port"], cidr=od["cidr"])
            ops.append(op)
            
        ec2 = get_ec2_client()
        try:
            sg_res = ec2.describe_security_groups(GroupIds=[sg_id])
            sg = sg_res["SecurityGroups"][0]
        except ClientError as e:
            if e.response["Error"]["Code"] == "InvalidGroup.NotFound":
                raise get_error("SG_NOT_FOUND", "SG not found")
            raise e
            
        tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}
        mkey = os.environ.get("MANAGED_TAG_KEY", "deadman:managed")
        mval = os.environ.get("MANAGED_TAG_VALUE", "true")
        skey = os.environ.get("STAGE_TAG_KEY", "deadman:stage")
        sval = os.environ.get("STAGE", "shared")
        
        if tags.get(mkey) != mval or tags.get(skey) != sval:
            raise get_error("SG_NOT_MANAGED", "SG lacks deadman:managed=true or the right deadman:stage")
            
        # Plan delta
        current_rules = sg.get("IpPermissions", [])
        # snapshot all rules
        snapshot = []
        for r in current_rules:
            for ip_range in r.get("IpRanges", []):
                snapshot.append({
                    "protocol": r.get("IpProtocol", "-1"),
                    "from_port": r.get("FromPort", -1),
                    "to_port": r.get("ToPort", -1),
                    "source_type": "cidr",
                    "source": ip_range.get("CidrIp"),
                    "description": ip_range.get("Description", "")
                })
                
        effective_ops = plan_delta(current_rules, ops)
        
        change_id = str(ulid.new())
        now = int(time.time())
        expires_at = now + ttl_seconds
        
        table = get_table()
        
        # T0
        try:
            table.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": table.name,
                            "Item": {
                                "pk": f"CHG#{change_id}",
                                "status": STATUS_PENDING,
                                "sg_id": sg_id,
                                "ttl_seconds": ttl_seconds,
                                "created_at": now,
                                "expires_at": expires_at,
                                "schedule_name": f"dm-{change_id}",
                                "snapshot": snapshot,
                                "delta": [o.to_dict() for o in ops],
                                "apply_done": False,
                                "purge_at": expires_at + 604800
                            },
                            "ConditionExpression": T0_CHG_COND
                        }
                    },
                    {
                        "Put": {
                            "TableName": table.name,
                            "Item": {
                                "pk": f"SGLOCK#{sg_id}",
                                "active_change_id": change_id,
                                "lock_until": expires_at + 360
                            },
                            "ConditionExpression": T0_SGLOCK_COND,
                            "ExpressionAttributeValues": {":now": now}
                        }
                    }
                ]
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                raise get_error("SG_BUSY", "Active change on this SG")
            raise e
            
        # CreateSchedule
        scheduler = get_scheduler_client()
        sched_group = os.environ.get("SCHEDULE_GROUP", f"deadman-{sval}")
        target_arn = os.environ.get("REVERT_FN_ARN")
        role_arn = os.environ.get("SCHEDULER_ROLE_ARN")
        
        dt = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        at_expr = f"at({dt.strftime('%Y-%m-%dT%H:%M:%S')})"
        
        try:
            scheduler.create_schedule(
                Name=f"dm-{change_id}",
                GroupName=sched_group,
                ScheduleExpression=at_expr,
                FlexibleTimeWindow={"Mode": "OFF"},
                ActionAfterCompletion="DELETE",
                Target={
                    "Arn": target_arn,
                    "RoleArn": role_arn,
                    "Input": json.dumps({"trigger": "SCHEDULE", "change_id": change_id}, cls=DecimalEncoder)
                }
            )
        except Exception as e:
            print("CREATE SCHEDULE ERROR:", e)
            # T8
            table.update_item(
                Key={"pk": f"CHG#{change_id}"},
                UpdateExpression="SET #st = :failed",
                ConditionExpression=T8_COND,
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":failed": STATUS_FAILED, ":P": STATUS_PENDING, ":false": False}
            )
            # release lock
            table.delete_item(Key={"pk": f"SGLOCK#{sg_id}"})
            raise get_error("SCHEDULE_FAILED", "Nothing applied", change_id, STATUS_FAILED)
            
        # T1 fence
        apply_lease = int(os.environ.get("APPLY_LEASE_SECONDS", "20"))
        try:
            table.update_item(
                Key={"pk": f"CHG#{change_id}"},
                UpdateExpression="SET apply_lease_until = :lu",
                ConditionExpression=T1_COND,
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={":P": STATUS_PENDING, ":false": False, ":lu": now + apply_lease}
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Lost to revert before cutting
                raise get_error("CHANGE_NOT_PENDING", "Lost race to revert", change_id, "REVERTED")
            raise e
            
        # Cut ops
        has_error = False
        try:
            for op in ops:
                ip_perm = {
                    "IpProtocol": op.protocol,
                    "FromPort": op.from_port,
                    "ToPort": op.to_port,
                    "IpRanges": [{"CidrIp": op.cidr}]
                }
                if op.action == "AUTHORIZE":
                    try:
                        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[ip_perm])
                        op.applied = True
                    except ClientError as e:
                        if e.response["Error"]["Code"] == "InvalidPermission.Duplicate":
                            op.applied = False
                        else:
                            raise e
                else: # REVOKE
                    try:
                        ec2.revoke_security_group_ingress(GroupId=sg_id, IpPermissions=[ip_perm])
                        op.applied = True
                    except ClientError as e:
                        if e.response["Error"]["Code"] == "InvalidPermission.NotFound":
                            op.applied = False
                        else:
                            raise e
        except Exception as e:
            has_error = True
            
        if has_error:
            # Apply failure -> T3
            try:
                table.update_item(
                    Key={"pk": f"CHG#{change_id}"},
                    UpdateExpression="SET #st = :r, revert_trigger = :trig, revert_owner = :me, revert_lease_until = :lu, apply_done = :true, delta = :delta",
                    ConditionExpression=T3_COND,
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={
                        ":P": STATUS_PENDING,
                        ":true": True,
                        ":false": False, # for T3 cond
                        ":r": "REVERTING",
                        ":trig": "APPLY_FAILURE",
                        ":me": context.aws_request_id if hasattr(context, "aws_request_id") else "apply-lambda",
                        ":lu": now + 60,
                        ":now": now,
                        ":delta": [o.to_dict() for o in ops]
                    }
                )
                # best effort delete schedule
                try:
                    scheduler.delete_schedule(Name=f"dm-{change_id}", GroupName=sched_group)
                except:
                    pass
                
                try:
                    lm = boto3.client('lambda', region_name=os.environ.get("AWS_REGION", "ap-south-1"))
                    lm.invoke(
                        FunctionName=os.environ.get("REVERT_FN_ARN"),
                        InvocationType="Event",
                        Payload=json.dumps({"trigger": "SCHEDULE", "change_id": change_id}, cls=DecimalEncoder)
                    )
                except:
                    pass
                    
                raise get_error("APPLY_FAILED_REVERT_INCOMPLETE", "Apply failed", change_id, "REVERTING")
            except ClientError as e:
                pass
                
            raise get_error("APPLY_FAILED_REVERTED", "Apply failed and reverted", change_id, "REVERTED")
            
        # T1b
        table.update_item(
            Key={"pk": f"CHG#{change_id}"},
            UpdateExpression="SET apply_done = :true, delta = :delta",
            ConditionExpression=T1B_COND,
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":P": STATUS_PENDING, ":true": True, ":delta": [o.to_dict() for o in ops]}
        )
        
        return {
            "statusCode": 201,
            "body": json.dumps({
                "change_id": change_id,
                "status": STATUS_PENDING,
                "sg_id": sg_id,
                "ttl_seconds": ttl_seconds,
                "created_at": now,
                "expires_at": expires_at,
                "server_time": now,
                "schedule_name": f"dm-{change_id}",
                "delta": [o.to_dict() for o in ops]
            })
        }
    except DeadmanError as e:
        return {"statusCode": e.http_status, "body": json.dumps(e.to_dict(), cls=DecimalEncoder)}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": {"code": "INTERNAL", "message": str(e)}})}
