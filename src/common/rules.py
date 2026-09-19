# src/common/rules.py
from .models import Rule, Op
from .errors import APIError
from .aws import get_ec2_client

def plan_delta(requested_ops, current_rules):
    """
    requested_ops: List[Op] for the requested additions/removals
    current_rules: List[Rule] currently on the SG
    Returns list of Op (only EFFECTIVE ops).
    Raises APIError(BAD_REQUEST) if RULE_ALREADY_EXISTS or RULE_NOT_FOUND
    """
    current_set = {r.to_tuple() for r in current_rules}
    effective_ops = []
    
    for op in requested_ops:
        rtup = op.rule.to_tuple()
        if op.action == "AUTHORIZE":
            if rtup in current_set:
                raise APIError("BAD_REQUEST", f"RULE_ALREADY_EXISTS: {rtup}")
            effective_ops.append(op)
        elif op.action == "REVOKE":
            if rtup not in current_set:
                raise APIError("BAD_REQUEST", f"RULE_NOT_FOUND: {rtup}")
            effective_ops.append(op)
            
    return effective_ops

def reconcile(op: Op, sg_id: str):
    if not op.applied:
        return
    ec2 = get_ec2_client()
    if op.action == "REVOKE":
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[op.rule.to_aws_dict()]
        )
    elif op.action == "AUTHORIZE":
        ec2.revoke_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[op.rule.to_aws_dict()]
        )
