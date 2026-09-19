"""
Security Group rule parsing, delta planning, and reconciliation logic.
Spec §4 State Machine & Reconcile Logic, Spec §5 API Contract.
"""
from typing import List, Dict, Any, Tuple, Optional
import botocore.exceptions

from common.errors import DeadmanError
from common.models import RuleOp, SnapshotRule, RevertReportItem
from common.states import RESULT_REVERTED, RESULT_SKIPPED, RESULT_ERROR


def make_tuple_key(protocol: str, from_port: int, to_port: int, source_type: str, source: str) -> str:
    """Canonical key: protocol/from/to/source_type/source"""
    proto_norm = str(protocol).lower()
    st_norm = str(source_type).lower()
    # Handle -1 protocol
    if proto_norm in ("-1", "all"):
        proto_norm = "-1"
        from_port = -1
        to_port = -1
    return f"{proto_norm}/{from_port}/{to_port}/{st_norm}/{source}"


def describe_sg_rules(ec2_client: Any, sg_id: str) -> List[Dict[str, Any]]:
    """
    Describe all ingress rules for a security group.
    Returns normalized list of dicts.
    """
    rules: List[Dict[str, Any]] = []
    try:
        paginator = ec2_client.get_paginator("describe_security_group_rules")
        for page in paginator.paginate(Filters=[{"Name": "group-id", "Values": [sg_id]}]):
            for r in page.get("SecurityGroupRules", []):
                if r.get("IsEgress", False):
                    continue
                proto = str(r.get("IpProtocol", "")).lower()
                fp = r.get("FromPort", -1)
                tp = r.get("ToPort", -1)
                cidr = r.get("CidrIpv4")
                rule_id = r.get("SecurityGroupRuleId")
                desc = r.get("Description", "")
                if cidr:
                    rules.append({
                        "rule_id": rule_id,
                        "protocol": proto,
                        "from_port": fp,
                        "to_port": tp,
                        "source_type": "cidr",
                        "source": cidr,
                        "description": desc or "",
                        "tuple_key": make_tuple_key(proto, fp, tp, "cidr", cidr),
                    })
    except (AttributeError, botocore.exceptions.ClientError):
        # Fallback to describe_security_groups if describe_security_group_rules is unavailable
        resp = ec2_client.describe_security_groups(GroupIds=[sg_id])
        sgs = resp.get("SecurityGroups", [])
        if not sgs:
            raise DeadmanError("SG_NOT_FOUND", f"Security group {sg_id} not found", status_code=404)
        for perm in sgs[0].get("IpPermissions", []):
            proto = str(perm.get("IpProtocol", "")).lower()
            fp = perm.get("FromPort", -1)
            tp = perm.get("ToPort", -1)
            for ipr in perm.get("IpRanges", []):
                cidr = ipr.get("CidrIp")
                desc = ipr.get("Description", "")
                if cidr:
                    rules.append({
                        "rule_id": None,
                        "protocol": proto,
                        "from_port": fp,
                        "to_port": tp,
                        "source_type": "cidr",
                        "source": cidr,
                        "description": desc or "",
                        "tuple_key": make_tuple_key(proto, fp, tp, "cidr", cidr),
                    })
    return rules


def plan_delta(snapshot_rules: List[Dict[str, Any]], requested_ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Plans delta operations.
    Returns only EFFECTIVE ops.
    Raises RULE_ALREADY_EXISTS (409) if AUTHORIZE requested for an existing tuple.
    Raises RULE_NOT_FOUND (409) if REVOKE requested for a missing tuple.
    """
    existing_keys = {r["tuple_key"] for r in snapshot_rules}
    planned_delta: List[Dict[str, Any]] = []
    seen_in_request = set()

    for idx, raw_op in enumerate(requested_ops, start=1):
        action = raw_op["action"]
        proto = str(raw_op["protocol"]).lower()
        fp = raw_op["from_port"]
        tp = raw_op["to_port"]
        cidr = raw_op["cidr"]
        t_key = make_tuple_key(proto, fp, tp, "cidr", cidr)

        if t_key in seen_in_request:
            raise DeadmanError("INVALID_REQUEST", f"Duplicate operation for tuple {t_key} in request", status_code=400)
        seen_in_request.add(t_key)

        if action == "AUTHORIZE":
            if t_key in existing_keys:
                raise DeadmanError("RULE_ALREADY_EXISTS", f"Rule {t_key} already exists in security group", status_code=409)
        elif action == "REVOKE":
            if t_key not in existing_keys:
                raise DeadmanError("RULE_NOT_FOUND", f"Rule {t_key} not found in security group", status_code=409)

        planned_delta.append({
            "op_id": idx,
            "action": action,
            "protocol": proto,
            "from_port": fp,
            "to_port": tp,
            "cidr": cidr,
            "applied": None,
            "tuple_key": t_key,
        })

    return planned_delta


def apply_cut_ops(ec2_client: Any, sg_id: str, delta: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Executes the cut operations against EC2.
    Marks applied = True (if executed) or False (if no-op at cut time).
    Raises exception on actual failure so caller can trigger error path.
    """
    updated_delta: List[Dict[str, Any]] = []
    for op in delta:
        action = op["action"]
        proto = op["protocol"]
        fp = op["from_port"]
        tp = op["to_port"]
        cidr = op["cidr"]
        ip_permission = {
            "IpProtocol": proto,
            "FromPort": int(fp),
            "ToPort": int(tp),
            "IpRanges": [{"CidrIp": cidr}],
        }
        if proto == "-1":
            ip_permission = {
                "IpProtocol": proto,
                "IpRanges": [{"CidrIp": cidr}],
            }

        op_copy = dict(op)
        try:
            if action == "AUTHORIZE":
                ec2_client.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[ip_permission]
                )
                op_copy["applied"] = True
            elif action == "REVOKE":
                ec2_client.revoke_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[ip_permission]
                )
                op_copy["applied"] = True
        except botocore.exceptions.ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            # If already exists or not found at cut time, treat as no-op and set applied = False
            if code in ("InvalidPermission.Duplicate", "RuleAlreadyExists"):
                op_copy["applied"] = False
            elif code in ("InvalidPermission.NotFound", "RuleNotFound"):
                op_copy["applied"] = False
            else:
                # Real error: record failure and bubble up
                op_copy["applied"] = False
                updated_delta.append(op_copy)
                raise e
        updated_delta.append(op_copy)
    return updated_delta


def reconcile_revert_ops(ec2_client: Any, sg_id: str, delta: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Performs per-op reconcile in reverse order, taking a fresh describe for each op.
    Spec §4:
    - ops in reverse order, fresh describe each; skip any op with applied == false
    cur = describe_rules(sg_id)
    if op.action == "AUTHORIZE": # undo = revoke
        present(cur, op.tuple) ? revoke_by_tuple(op) : SKIPPED_ALREADY_SATISFIED
    else: # REVOKE, undo = authorize
        present(cur, op.tuple) ? SKIPPED_ALREADY_SATISFIED
                               : authorize(op) # Duplicate error -> SKIPPED

    Returns (final_status, revert_report).
    Never raises on transient errors, always records an outcome per op.
    """
    revert_report: List[Dict[str, Any]] = []
    # Reverse order per spec
    reversed_ops = list(reversed(delta))

    for op in reversed_ops:
        op_id = op["op_id"]
        action = op["action"]
        proto = op["protocol"]
        fp = op["from_port"]
        tp = op["to_port"]
        cidr = op["cidr"]
        t_key = op.get("tuple_key") or make_tuple_key(proto, fp, tp, "cidr", cidr)

        # Honour applied == false
        if op.get("applied") is False:
            revert_report.append({
                "op_id": op_id,
                "result": RESULT_SKIPPED,
                "detail": "Op marked applied=false, skipped undo",
            })
            continue

        try:
            # Fresh describe each op
            cur_rules = describe_sg_rules(ec2_client, sg_id)
            cur_keys = {r["tuple_key"] for r in cur_rules}
            present = t_key in cur_keys

            ip_permission = {
                "IpProtocol": proto,
                "FromPort": int(fp),
                "ToPort": int(tp),
                "IpRanges": [{"CidrIp": cidr}],
            }
            if proto == "-1":
                ip_permission = {
                    "IpProtocol": proto,
                    "IpRanges": [{"CidrIp": cidr}],
                }

            if action == "AUTHORIZE":
                # Original cut was AUTHORIZE, undo is REVOKE
                if present:
                    ec2_client.revoke_security_group_ingress(
                        GroupId=sg_id,
                        IpPermissions=[ip_permission]
                    )
                    revert_report.append({
                        "op_id": op_id,
                        "result": RESULT_REVERTED,
                        "detail": "",
                    })
                else:
                    revert_report.append({
                        "op_id": op_id,
                        "result": RESULT_SKIPPED,
                        "detail": "Rule not present in security group",
                    })
            else:
                # Original cut was REVOKE, undo is AUTHORIZE
                if present:
                    revert_report.append({
                        "op_id": op_id,
                        "result": RESULT_SKIPPED,
                        "detail": "Rule already present in security group",
                    })
                else:
                    try:
                        ec2_client.authorize_security_group_ingress(
                            GroupId=sg_id,
                            IpPermissions=[ip_permission]
                        )
                        revert_report.append({
                            "op_id": op_id,
                            "result": RESULT_REVERTED,
                            "detail": "",
                        })
                    except botocore.exceptions.ClientError as ce:
                        code = ce.response.get("Error", {}).get("Code", "")
                        if code in ("InvalidPermission.Duplicate", "RuleAlreadyExists"):
                            revert_report.append({
                                "op_id": op_id,
                                "result": RESULT_SKIPPED,
                                "detail": f"Duplicate ignored: {ce}",
                            })
                        else:
                            revert_report.append({
                                "op_id": op_id,
                                "result": RESULT_ERROR,
                                "detail": str(ce),
                            })
        except Exception as e:
            revert_report.append({
                "op_id": op_id,
                "result": RESULT_ERROR,
                "detail": str(e),
            })

    # Sort revert_report back by op_id ascending for consistent API output
    revert_report.sort(key=lambda r: r["op_id"])

    # Determine final status
    results = {r["result"] for r in revert_report}
    if not results or results.issubset({RESULT_REVERTED, RESULT_SKIPPED}):
        final_status = "REVERTED"
    elif RESULT_ERROR in results and (RESULT_REVERTED in results or RESULT_SKIPPED in results):
        final_status = "PARTIAL_REVERT"
    else:
        # All errored
        final_status = "FAILED"

    return final_status, revert_report
