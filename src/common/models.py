# src/common/models.py
from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class Rule:
    protocol: str
    from_port: int
    to_port: int
    source_type: str # 'cidr' or 'sg'
    source: str

    def to_tuple(self):
        return (self.protocol, self.from_port, self.to_port, self.source_type, self.source)
    
    def to_aws_dict(self):
        # Convert to boto3 IpPermissions format
        perm = {
            "IpProtocol": self.protocol,
            "FromPort": self.from_port,
            "ToPort": self.to_port,
        }
        if self.source_type == "cidr":
            perm["IpRanges"] = [{"CidrIp": self.source}]
        elif self.source_type == "sg":
            perm["UserIdGroupPairs"] = [{"GroupId": self.source}]
        return perm

    @classmethod
    def from_aws_dict(cls, d: dict, source_type: str, source: str):
        return cls(
            protocol=d.get("IpProtocol", "-1"),
            from_port=d.get("FromPort", -1),
            to_port=d.get("ToPort", -1),
            source_type=source_type,
            source=source
        )

@dataclass
class Op:
    action: str # "AUTHORIZE" or "REVOKE"
    rule: Rule
    applied: bool = False

@dataclass
class Change:
    id: str
    sg_id: str
    status: str
    ops: List[Op]
    purge_at: int
