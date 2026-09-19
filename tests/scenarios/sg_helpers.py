"""boto3 helpers against deadman-target-<stage> (real AWS only; MOCK=1 uses FakeSG in run.py).

Never called unless the runner is in real mode. Reads no secrets; credentials come from the
standard boto3 chain.
"""
import time

import boto3

REGION = "ap-south-1"
BASELINE = ("tcp", 80, 80, "0.0.0.0/0")   # the rule the scenarios revoke
TEST_PORTS = (8081, 8443)                  # ports used by S4/S9; removed on reset


def _perm(proto, lo, hi, cidr):
    return [{"IpProtocol": proto, "FromPort": lo, "ToPort": hi, "IpRanges": [{"CidrIp": cidr}]}]


class RealSG:
    def __init__(self, sg_id, region=REGION):
        self.sg_id = sg_id
        self.ec2 = boto3.client("ec2", region_name=region)

    def rules(self):
        """Set of (proto, from, to, cidr) for IPv4 ingress rules."""
        out = set()
        sg = self.ec2.describe_security_groups(GroupIds=[self.sg_id])["SecurityGroups"][0]
        for p in sg["IpPermissions"]:
            for r in p.get("IpRanges", []):
                out.add((p["IpProtocol"], p.get("FromPort"), p.get("ToPort"), r["CidrIp"]))
        return out

    def has(self, rule):
        return rule in self.rules()

    def add(self, rule):
        try:
            self.ec2.authorize_security_group_ingress(GroupId=self.sg_id, IpPermissions=_perm(*rule))
        except Exception as e:
            if "Duplicate" not in str(e):
                raise

    def remove(self, rule):
        try:
            self.ec2.revoke_security_group_ingress(GroupId=self.sg_id, IpPermissions=_perm(*rule))
        except Exception as e:
            if "NotFound" not in str(e):
                raise

    def reset(self):
        """Back to a known baseline: tcp/80 open, scenario test ports closed."""
        for port in TEST_PORTS:
            for cidr in ("0.0.0.0/0", "198.51.100.0/24"):
                self.remove(("tcp", port, port, cidr))
        self.add(BASELINE)

    def create_unmanaged_sg(self):
        """Temporary SG in the same VPC, deliberately without deadman tags (S8). Returns its id."""
        vpc = self.ec2.describe_security_groups(GroupIds=[self.sg_id])["SecurityGroups"][0]["VpcId"]
        r = self.ec2.create_security_group(
            GroupName=f"deadman-m4-unmanaged-{int(time.time())}", Description="m4 scenario S8 (temporary)",
            VpcId=vpc)
        return r["GroupId"]

    def delete_sg(self, sg_id):
        for _ in range(5):
            try:
                self.ec2.delete_security_group(GroupId=sg_id)
                return
            except Exception:
                time.sleep(2)
