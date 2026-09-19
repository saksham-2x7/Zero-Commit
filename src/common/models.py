"""
Data models and serialization helpers for Deadman.
Spec §3 DynamoDB Schema, §5 API Contract.
"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
import ipaddress
import re

from common.errors import DeadmanError

RE_SG_ID = re.compile(r"^sg-[0-9a-fA-F]+$")


@dataclass
class RuleOp:
    op_id: int
    action: str  # "AUTHORIZE" | "REVOKE"
    protocol: str  # "tcp", "udp", "icmp", "-1"
    from_port: int
    to_port: int
    cidr: str  # IPv4 CIDR
    applied: Optional[bool] = None

    def tuple_key(self) -> str:
        """Normalized tuple key: protocol/from/to/source_type/source"""
        return f"{self.protocol.lower()}/{self.from_port}/{self.to_port}/cidr/{self.cidr}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SnapshotRule:
    protocol: str
    from_port: int
    to_port: int
    source_type: str
    source: str
    description: str = ""
    rule_id: Optional[str] = None

    def tuple_key(self) -> str:
        return f"{self.protocol.lower()}/{self.from_port}/{self.to_port}/{self.source_type.lower()}/{self.source}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RevertReportItem:
    op_id: int
    result: str  # "REVERTED" | "SKIPPED_ALREADY_SATISFIED" | "ERROR"
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_cidr(cidr_str: str) -> None:
    try:
        net = ipaddress.ip_network(cidr_str, strict=False)
        if net.version != 4:
            raise DeadmanError("UNSUPPORTED_RULE", "Only IPv4 CIDR rules are supported", status_code=400)
    except DeadmanError:
        raise
    except Exception as e:
        raise DeadmanError("UNSUPPORTED_RULE", f"Invalid CIDR: {e}", status_code=400)


def validate_create_payload(data: Any, min_ttl: int, max_ttl: int) -> None:
    if not isinstance(data, dict):
        raise DeadmanError("INVALID_REQUEST", "Request body must be a JSON object", status_code=400)

    sg_id = data.get("sg_id")
    if not sg_id or not isinstance(sg_id, str) or not RE_SG_ID.match(sg_id):
        raise DeadmanError("INVALID_REQUEST", "sg_id must be a valid security group ID (e.g. sg-0abc...)", status_code=400)

    ttl = data.get("ttl_seconds")
    if ttl is None or not isinstance(ttl, int):
        raise DeadmanError("INVALID_REQUEST", "ttl_seconds must be an integer", status_code=400)
    if ttl < min_ttl or ttl > max_ttl:
        raise DeadmanError("TTL_OUT_OF_RANGE", f"ttl_seconds must be between {min_ttl} and {max_ttl}", status_code=400)

    ops = data.get("ops")
    if not isinstance(ops, list) or len(ops) < 1 or len(ops) > 5:
        raise DeadmanError("INVALID_REQUEST", "ops must be a list of 1 to 5 rule operations", status_code=400)

    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise DeadmanError("INVALID_REQUEST", f"op {i} must be a JSON object", status_code=400)
        action = op.get("action")
        if action not in ("AUTHORIZE", "REVOKE"):
            raise DeadmanError("INVALID_REQUEST", f"op {i} action must be AUTHORIZE or REVOKE", status_code=400)

        proto = op.get("protocol")
        if not isinstance(proto, str) or proto.lower() not in ("tcp", "udp", "icmp", "-1"):
            raise DeadmanError("INVALID_REQUEST", f"op {i} invalid protocol: {proto}", status_code=400)

        fp = op.get("from_port")
        tp = op.get("to_port")
        if not isinstance(fp, int) or not isinstance(tp, int):
            raise DeadmanError("INVALID_REQUEST", f"op {i} from_port and to_port must be integers", status_code=400)

        cidr = op.get("cidr")
        if not cidr or not isinstance(cidr, str):
            raise DeadmanError("UNSUPPORTED_RULE", f"op {i} cidr must be specified as an IPv4 CIDR string", status_code=400)
        validate_cidr(cidr)
