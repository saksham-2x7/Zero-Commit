# src/common/ddb.py
import json
import os
from .aws import get_ddb_client
from .models import Change, Op, Rule

def get_table_name():
    return os.environ.get("TABLE_NAME", "deadman-table")

def _serialize_ops(ops):
    s = []
    for op in ops:
        s.append({
            "action": op.action,
            "applied": op.applied,
            "rule": op.rule.to_aws_dict(),
            "source_type": op.rule.source_type,
            "source": op.rule.source
        })
    return json.dumps(s)

def _deserialize_ops(ops_str):
    s = json.loads(ops_str)
    ops = []
    for d in s:
        rule = Rule.from_aws_dict(d["rule"], d["source_type"], d["source"])
        ops.append(Op(action=d["action"], rule=rule, applied=d["applied"]))
    return ops

def put_change(change: Change, condition_expr=None, condition_vals=None):
    ddb = get_ddb_client()
    item = {
        "pk": {"S": change.id},
        "sg_id": {"S": change.sg_id},
        "st": {"S": change.status},
        "ops": {"S": _serialize_ops(change.ops)},
        "purge_at": {"N": str(change.purge_at)}
    }
    kwargs = {
        "TableName": get_table_name(),
        "Item": item
    }
    if condition_expr:
        kwargs["ConditionExpression"] = condition_expr
    if condition_vals:
        exp_attr_vals = {}
        for k, v in condition_vals.items():
            exp_attr_vals[k] = {"S": v}
        kwargs["ExpressionAttributeValues"] = exp_attr_vals

    ddb.put_item(**kwargs)

def get_change(change_id: str):
    ddb = get_ddb_client()
    res = ddb.get_item(
        TableName=get_table_name(),
        Key={"pk": {"S": change_id}}
    )
    item = res.get("Item")
    if not item:
        return None
    return Change(
        id=item["pk"]["S"],
        sg_id=item["sg_id"]["S"],
        status=item["st"]["S"],
        ops=_deserialize_ops(item["ops"]["S"]),
        purge_at=int(item["purge_at"]["N"])
    )

def update_change_status(change_id: str, new_status: str, condition_expr=None, condition_vals=None):
    ddb = get_ddb_client()
    kwargs = {
        "TableName": get_table_name(),
        "Key": {"pk": {"S": change_id}},
        "UpdateExpression": "SET st = :new_st",
    }
    exp_attr_vals = {":new_st": {"S": new_status}}
    
    if condition_expr:
        kwargs["ConditionExpression"] = condition_expr
    if condition_vals:
        for k, v in condition_vals.items():
            exp_attr_vals[k] = {"S": v}
    
    kwargs["ExpressionAttributeValues"] = exp_attr_vals
    
    ddb.update_item(**kwargs)
