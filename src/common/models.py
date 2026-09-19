from dataclasses import dataclass
from typing import Optional, List, Dict, Any

@dataclass
class Op:
    op_id: int
    action: str
    protocol: str
    from_port: int
    to_port: int
    cidr: str
    applied: Optional[bool] = None

    @property
    def tuple_key(self):
        return (self.protocol, self.from_port, self.to_port, "cidr", self.cidr)

    def to_dict(self):
        return {
            "op_id": self.op_id,
            "action": self.action,
            "protocol": self.protocol,
            "from_port": self.from_port,
            "to_port": self.to_port,
            "cidr": self.cidr,
            "applied": self.applied
        }
    
    @classmethod
    def from_dict(cls, d):
        return cls(
            op_id=int(d["op_id"]),
            action=d["action"],
            protocol=d["protocol"],
            from_port=int(d["from_port"]),
            to_port=int(d["to_port"]),
            cidr=d["cidr"],
            applied=d.get("applied")
        )
