#!/usr/bin/env python3
"""Spec checks 1c and 1d (owner: M4).

  1c  SecurityGroupRuleId on re-add (A-9), revoke with a different Description
      (A-10), describe visibility latency after authorize (A-11).
  1d  Which EC2 actions honour aws:ResourceTag/deadman:managed and
      aws:ResourceTag/deadman:stage (A-13), and which need Resource "*" (A-14).

Real run (AWS credentials required; creates only temporary resources):
    python probes/sg_probe.py --yes-aws [--stage m4] [--region ap-south-1] [--write-md]

Dry run against moto, to find bugs in this script only:
    python probes/sg_probe.py --moto

Moto results are NEVER PASS/FAIL evidence: moto does not evaluate IAM policies
or model EC2 propagation delay. In --moto mode every verdict is printed as
"NOT RUN (moto dry-run)" and docs/verification_m4.md is never written.
"""
import argparse
import json
import re
import sys
import time
import uuid

import boto3
from botocore.exceptions import ClientError

MANAGED_KEY, MANAGED_VAL, STAGE_KEY = "deadman:managed", "true", "deadman:stage"
PROBE_TAG = ("deadman:probe", "m4-sg-probe")  # marks temp resources for cleanup
CIDR = "203.0.113.0/24"  # TEST-NET-3
TRIALS_1C_II = 20
VISIBLE_WITHIN_S = 5.0
WRONG_STAGE = "zz"


def perm(port, cidr=CIDR, desc=None):
    r = {"CidrIp": cidr}
    if desc is not None:
        r["Description"] = desc
    return [{"IpProtocol": "tcp", "FromPort": port, "ToPort": port, "IpRanges": [r]}]


def code_of(e):
    return e.response.get("Error", {}).get("Code", "?") if isinstance(e, ClientError) else type(e).__name__


def find_rule(ec2, sg_id, port, cidr=CIDR):
    """Return SecurityGroupRuleId of the ingress tcp/port/cidr rule, or None."""
    pages = ec2.get_paginator("describe_security_group_rules").paginate(
        Filters=[{"Name": "group-id", "Values": [sg_id]}])
    for page in pages:
        for r in page["SecurityGroupRules"]:
            if (not r["IsEgress"] and r.get("IpProtocol") == "tcp" and r.get("FromPort") == port
                    and r.get("ToPort") == port and r.get("CidrIpv4") == cidr):
                return r["SecurityGroupRuleId"]
    return None


def authorize_id(ec2, sg_id, port, desc=None):
    resp = ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=perm(port, desc=desc))
    rules = resp.get("SecurityGroupRules") or []
    return rules[0]["SecurityGroupRuleId"] if rules else find_rule(ec2, sg_id, port)


# ---------------------------------------------------------------- resources
class Temp:
    """Creates and always cleans up temporary SGs and IAM role."""

    def __init__(self, session, region, stage):
        self.ec2 = session.client("ec2", region_name=region)
        self.iam = session.client("iam")
        self.sts = session.client("sts", region_name=region)
        self.stage, self.sgs, self.role, self.policies = stage, [], None, []
        self.run = uuid.uuid4().hex[:8]

    def vpc(self):
        v = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        if not v:
            raise RuntimeError("no default VPC in this region")
        return v[0]["VpcId"]

    def sg(self, label, tags):
        r = self.ec2.create_security_group(
            GroupName=f"deadman-probe-{label}-{self.run}", Description="deadman m4 probe (temporary)",
            VpcId=self.vpc(),
            TagSpecifications=[{"ResourceType": "security-group",
                                "Tags": [{"Key": k, "Value": v} for k, v in tags + [PROBE_TAG]]}])
        self.sgs.append(r["GroupId"])
        return r["GroupId"]

    def cleanup(self):
        errs = []
        for p in self.policies:
            try:
                self.iam.delete_role_policy(RoleName=self.role, PolicyName=p)
            except Exception as e:
                errs.append(f"policy {p}: {code_of(e)}")
        if self.role:
            try:
                self.iam.delete_role(RoleName=self.role)
            except Exception as e:
                errs.append(f"role: {code_of(e)}")
        for sg in self.sgs:
            for _ in range(5):
                try:
                    self.ec2.delete_security_group(GroupId=sg)
                    break
                except ClientError as e:
                    if code_of(e) == "InvalidGroup.NotFound":
                        break
                    time.sleep(2)
            else:
                errs.append(f"sg {sg} not deleted")
        return errs


# ---------------------------------------------------------------- check 1c
def check_1c(t, moto):
    ev, sg = {}, t.sg("1c", [(MANAGED_KEY, MANAGED_VAL), (STAGE_KEY, t.stage)])
    ec2 = t.ec2
    try:
        a = authorize_id(ec2, sg, 8080)
        ec2.revoke_security_group_ingress(GroupId=sg, IpPermissions=perm(8080))
        b = authorize_id(ec2, sg, 8080)
        ev["A"], ev["B"], ev["A_ne_B"] = a, b, bool(a and b and a != b)
        ec2.revoke_security_group_ingress(GroupId=sg, IpPermissions=perm(8080))

        # (i) revoke with a different Description (A-10)
        authorize_id(ec2, sg, 8081, desc="original")
        try:
            ec2.revoke_security_group_ingress(GroupId=sg, IpPermissions=perm(8081, desc="DIFFERENT"))
            ev["i_call_error"] = None
        except ClientError as e:
            ev["i_call_error"] = code_of(e)
        time.sleep(1)
        ev["i_removed"] = find_rule(ec2, sg, 8081) is None
        if not ev["i_removed"]:
            ec2.revoke_security_group_ingress(GroupId=sg, IpPermissions=perm(8081))

        # (ii) describe visibility after authorize, 20 trials (A-11)
        lat = []
        for i in range(TRIALS_1C_II):
            port = 9000 + i
            t0 = time.monotonic()
            ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=perm(port))
            seen = None
            while time.monotonic() - t0 < 15:
                if find_rule(ec2, sg, port):
                    seen = time.monotonic() - t0
                    break
                time.sleep(0.1)
            lat.append(seen)
        ev["ii_latencies_s"] = [None if x is None else round(x, 3) for x in lat]
        ok = [x for x in lat if x is not None and x <= VISIBLE_WITHIN_S]
        ev["ii_within_5s"] = f"{len(ok)}/{TRIALS_1C_II}"
        ev["ii_max_s"] = None if None in lat else round(max(lat), 3)
    except Exception as e:  # any unexpected error means the check could not be evaluated
        ev["error"] = f"{code_of(e)}: {e}"
        return verdict("1c", None, ev, moto)
    parts = {
        "A-9 (A != B)": ev["A_ne_B"],
        "A-10 (revoke with different Description removes rule)": ev["i_removed"],
        "A-11 (visible <5 s in 20/20)": len(ok) == TRIALS_1C_II,
    }
    ev["assumptions"] = parts
    return verdict("1c", all(parts.values()), ev, moto)


# ---------------------------------------------------------------- check 1d
ACTIONS = ["AuthorizeSecurityGroupIngress", "RevokeSecurityGroupIngress",
           "DescribeSecurityGroups", "DescribeSecurityGroupRules"]


def put_role(t, tag_condition_policy, describe_star_policy):
    ident = t.sts.get_caller_identity()
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
             "Principal": {"AWS": f"arn:aws:iam::{ident['Account']}:root"}, "Action": "sts:AssumeRole"}]}
    t.role = f"deadman-probe-1d-{t.run}"
    t.iam.create_role(RoleName=t.role, AssumeRolePolicyDocument=json.dumps(trust),
                      Tags=[{"Key": PROBE_TAG[0], "Value": PROBE_TAG[1]}])
    for name, doc in (("tagcond", tag_condition_policy), ("describestar", describe_star_policy)):
        t.iam.put_role_policy(RoleName=t.role, PolicyName=name, PolicyDocument=json.dumps(doc))
        t.policies.append(name)
    return f"arn:aws:iam::{ident['Account']}:role/{t.role}"


def assume(t, arn, session_name):
    for _ in range(12):  # IAM is eventually consistent
        try:
            c = t.sts.assume_role(RoleArn=arn, RoleSessionName=session_name)["Credentials"]
            return boto3.Session(aws_access_key_id=c["AccessKeyId"], aws_secret_access_key=c["SecretAccessKey"],
                                 aws_session_token=c["SessionToken"], region_name=t.ec2.meta.region_name)
        except ClientError:
            time.sleep(5)
    raise RuntimeError("could not assume probe role")


def attempt(fn):
    try:
        fn()
        return "ALLOWED"
    except ClientError as e:
        c = code_of(e)
        return "DENIED" if c in ("UnauthorizedOperation", "AccessDenied", "AccessDeniedException") else f"ERR:{c}"


def check_1d(t, moto):
    ev = {}
    good = t.sg("good", [(MANAGED_KEY, MANAGED_VAL), (STAGE_KEY, t.stage)])
    wrong = t.sg("wrongstage", [(MANAGED_KEY, MANAGED_VAL), (STAGE_KEY, WRONG_STAGE)])
    unman = t.sg("untagged", [])
    cond = {"StringEquals": {f"aws:ResourceTag/{MANAGED_KEY}": MANAGED_VAL, f"aws:ResourceTag/{STAGE_KEY}": t.stage}}
    # Policy P1: every action scoped to security-group/* WITH the tag conditions.
    # If an action is not resource-level for tags, P1 alone will not allow it even on the good SG.
    p1 = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": [f"ec2:{a}" for a in ACTIONS],
          "Resource": "arn:aws:ec2:*:*:security-group/*", "Condition": cond}]}
    # Policy P2: Describe* on "*" without conditions (the A-14 expectation).
    p2 = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
          "Action": ["ec2:DescribeSecurityGroups", "ec2:DescribeSecurityGroupRules"], "Resource": "*"}]}
    try:
        arn = put_role(t, p1, p2)
        # Seed one rule on each SG as admin so Revoke is meaningful everywhere.
        for sg in (good, wrong, unman):
            t.ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=perm(7001))
        # Full matrix (P1 + P2 together: what the real role would have).
        s = assume(t, arn, "full")
        e = s.client("ec2", region_name=t.ec2.meta.region_name)
        m = {}
        for name, sg in (("good", good), ("wrong_stage", wrong), ("untagged", unman)):
            m[name] = {
                "AuthorizeSecurityGroupIngress": attempt(lambda: e.authorize_security_group_ingress(
                    GroupId=sg, IpPermissions=perm(7002))),
                "RevokeSecurityGroupIngress": attempt(lambda: e.revoke_security_group_ingress(
                    GroupId=sg, IpPermissions=perm(7001))),
                "DescribeSecurityGroups": attempt(lambda: e.describe_security_groups(GroupIds=[sg])),
                "DescribeSecurityGroupRules": attempt(lambda: e.describe_security_group_rules(
                    Filters=[{"Name": "group-id", "Values": [sg]}])),
            }
        ev["matrix_p1_plus_p2"] = m
        # Resource-level test for Describe*: P1 only (drop P2).
        t.iam.delete_role_policy(RoleName=t.role, PolicyName="describestar")
        t.policies.remove("describestar")
        time.sleep(15)
        s1 = assume(t, arn, "p1only")
        e1 = s1.client("ec2", region_name=t.ec2.meta.region_name)
        ev["describe_with_p1_only_on_good_sg"] = {
            "DescribeSecurityGroups": attempt(lambda: e1.describe_security_groups(GroupIds=[good])),
            "DescribeSecurityGroupRules": attempt(lambda: e1.describe_security_group_rules(
                Filters=[{"Name": "group-id", "Values": [good]}])),
        }
    except Exception as ex:
        ev["error"] = f"{code_of(ex)}: {ex}"
        return verdict("1d", None, ev, moto)
    mutating = ("AuthorizeSecurityGroupIngress", "RevokeSecurityGroupIngress")
    crit = {
        "mutations allowed on correctly tagged SG": all(m["good"][a] == "ALLOWED" for a in mutating),
        "mutations denied on wrong-stage SG": all(m["wrong_stage"][a] == "DENIED" for a in mutating),
        "mutations denied on untagged SG": all(m["untagged"][a] == "DENIED" for a in mutating),
    }
    ev["criteria"] = crit
    ev["describe_needs_star_A14"] = any(v != "ALLOWED" for v in ev["describe_with_p1_only_on_good_sg"].values())
    return verdict("1d", all(crit.values()), ev, moto)


# ---------------------------------------------------------------- reporting
def verdict(check, ok, ev, moto):
    if moto:
        status = "NOT RUN (moto dry-run)"
    elif ok is None:
        status = "NOT RUN (probe error, see evidence)"
    else:
        status = "PASS" if ok else "FAIL"
    return {"check": check, "status": status, "evidence": ev}


def render_md(res):
    return (f"**Status: {res['status']}**\n\n```json\n"
            f"{json.dumps(res['evidence'], indent=2, sort_keys=True)}\n```\n")


def write_md(path, results):
    txt = open(path, encoding="utf-8").read()
    for r in results:
        pat = re.compile(rf"(<!-- {r['check']}:begin -->).*?(<!-- {r['check']}:end -->)", re.S)
        if not pat.search(txt):
            sys.exit(f"markers for {r['check']} missing in {path}")
        txt = pat.sub(lambda m: f"{m.group(1)}\n{render_md(r)}{m.group(2)}", txt)
    open(path, "w", encoding="utf-8").write(txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--moto", action="store_true", help="dry-run against moto (never recorded)")
    ap.add_argument("--yes-aws", action="store_true", help="required to touch real AWS")
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--stage", default="m4")
    ap.add_argument("--checks", default="1c,1d")
    ap.add_argument("--write-md", action="store_true", help="update docs/verification_m4.md (real AWS only)")
    ap.add_argument("--md", default="docs/verification_m4.md")
    a = ap.parse_args()
    if not a.moto and not a.yes_aws:
        sys.exit("Refusing to call AWS without --yes-aws (use --moto for a dry run).")
    if a.moto and a.write_md:
        sys.exit("--write-md is not allowed with --moto: moto results are never evidence.")

    ctx = None
    if a.moto:
        import os
        from moto import mock_aws
        for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            os.environ[k] = "testing"
        ctx = mock_aws()
        ctx.start()
    t = Temp(boto3.Session(region_name=a.region), a.region, a.stage)
    results = []
    try:
        for c in a.checks.split(","):
            results.append({"1c": check_1c, "1d": check_1d}[c.strip()](t, a.moto))
    finally:
        errs = t.cleanup()
        if ctx:
            ctx.stop()
    if errs:
        print("CLEANUP INCOMPLETE, remove manually (tag deadman:probe=m4-sg-probe):", errs, file=sys.stderr)
    for r in results:
        print(f"== {r['check']}: {r['status']}\n{json.dumps(r['evidence'], indent=2, sort_keys=True)}")
    if a.write_md and not a.moto:
        write_md(a.md, results)
        print(f"wrote {a.md}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
