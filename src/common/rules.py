from .models import Op
from .errors import get_error

def _get_tuple_key(rule):
    source = "cidr"
    cidr = rule.get("CidrIpv4")
    if not cidr:
        # We only support IPv4 CIDR per spec
        return None
    return (
        rule.get("IpProtocol", "-1"),
        rule.get("FromPort", -1),
        rule.get("ToPort", -1),
        source,
        cidr
    )

def plan_delta(current_rules: list, ops: list[Op]):
    # returns effective ops or raises Error
    current_tuples = set(_get_tuple_key(r) for r in current_rules if _get_tuple_key(r))
    
    effective_ops = []
    for op in ops:
        tk = op.tuple_key
        if op.action == "AUTHORIZE":
            if tk in current_tuples:
                raise get_error("RULE_ALREADY_EXISTS", f"Rule {tk} already exists")
            effective_ops.append(op)
        elif op.action == "REVOKE":
            if tk not in current_tuples:
                raise get_error("RULE_NOT_FOUND", f"Rule {tk} not found")
            effective_ops.append(op)
    
    return effective_ops

def reconcile_op(op: Op, current_rules: list, authorize_fn, revoke_fn):
    if op.applied is False:
        return "SKIPPED_ALREADY_SATISFIED"
        
    current_tuples = set(_get_tuple_key(r) for r in current_rules if _get_tuple_key(r))
    tk = op.tuple_key
    
    try:
        if op.action == "AUTHORIZE":
            # undo = revoke
            if tk in current_tuples:
                revoke_fn(op)
                return "REVERTED"
            else:
                return "SKIPPED_ALREADY_SATISFIED"
        else:
            # REVOKE, undo = authorize
            if tk in current_tuples:
                return "SKIPPED_ALREADY_SATISFIED"
            else:
                try:
                    authorize_fn(op)
                    return "REVERTED"
                except Exception as e:
                    if "Duplicate" in str(e): # pseudo check for duplicate error
                        return "SKIPPED_ALREADY_SATISFIED"
                    raise e
    except Exception as e:
        raise e
