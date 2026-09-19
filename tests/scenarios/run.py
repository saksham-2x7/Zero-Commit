#!/usr/bin/env python3
"""Scenario runner S1-S10 (owner: M4).

Real:  BASE_URL=https://<cf-domain> TOKEN=... SG_ID=sg-... python tests/scenarios/run.py [S1 S4 ...]
Mock:  MOCK=1 python tests/scenarios/run.py         (in-process fake; only debugs this runner)
Other: --check-fixtures  compares docs/fixtures/*.json with spec §5 (no network, no AWS)

Env: BASE_URL (no trailing /api; paths below include /api), TOKEN, SG_ID, TTL_SECONDS (default MIN_TTL_SECONDS),
     MIN_TTL_SECONDS (60), MAX_TTL_SECONDS (600), RACE_ROUNDS (3), REGION (ap-south-1).
Exit code 1 if any scenario FAILs. INCONCLUSIVE is reported separately and never counted as PASS.
"""
import glob
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_api import FakeAPI, FakeClock, FakeSG  # noqa: E402

RULE = ("tcp", 80, 80, "0.0.0.0/0")
OP80 = {"action": "REVOKE", "protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}
OP_8081 = {"action": "AUTHORIZE", "protocol": "tcp", "from_port": 8081, "to_port": 8081, "cidr": "0.0.0.0/0"}
RULE_8443 = ("tcp", 8443, 8443, "198.51.100.0/24")
SLACK = 60          # seconds beyond ttl we wait for the scheduler before failing S2
EXPIRED_CODES = {"WINDOW_EXPIRED", "CHANGE_NOT_PENDING", "REVERT_IN_PROGRESS"}


class Fail(Exception):
    pass


class Inconclusive(Exception):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


# ------------------------------------------------------------------ environments
class HttpEnv:
    mock = False

    def __init__(self):
        self.base = os.environ["BASE_URL"].rstrip("/")
        self.token = os.environ["TOKEN"]
        from sg_helpers import RealSG
        self.sg = RealSG(os.environ["SG_ID"], os.environ.get("REGION", "ap-south-1"))
        self.sg_id = os.environ["SG_ID"]

    now = staticmethod(time.time)
    sleep = staticmethod(time.sleep)

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        try:
            js = json.loads(raw or b"{}")
        except ValueError:
            js = {"_raw": raw[:200].decode("utf-8", "replace")}
        return status, js, time.monotonic() - t0


class MockEnv:
    mock = True

    def __init__(self):
        self.clock = FakeClock()
        self.sg = FakeSG()
        self.sg_id = self.sg.sg_id
        self.api = FakeAPI(self.clock, self.sg)
        self.now, self.sleep = self.clock.now, self.clock.sleep

    def call(self, method, path, body=None):
        s, j = self.api.request(method, path, body)
        return s, j, 0.0


def apply(env, ops=None, ttl=None, sg_id=None):
    return env.call("POST", "/api/changes", {"sg_id": sg_id or env.sg_id, "ttl_seconds": ttl or env.ttl,
                                             "ops": ops or [OP80]})


def new_change(env, **kw):
    s, j, dt = apply(env, **kw)
    check(s == 201, f"apply expected 201, got {s} {j}")
    check(j.get("status") == "PENDING" and j.get("change_id"), f"bad apply body {j}")
    return j["change_id"], j


def get(env, cid):
    s, j, _ = env.call("GET", f"/api/changes/{cid}")
    check(s == 200, f"GET expected 200, got {s} {j}")
    return j


def wait_status(env, cid, want, timeout):
    t0 = env.now()
    while env.now() - t0 <= timeout:
        j = get(env, cid)
        if j["status"] in want:
            return j, env.now() - t0
        env.sleep(1)
    raise Fail(f"status never reached {want} within {timeout}s (last {j['status']})")


def err_code(j):
    e = j.get("error")
    return e.get("code") if isinstance(e, dict) else e


# ------------------------------------------------------------------ scenarios
def s1_confirm(env):
    cid, j = new_change(env)
    check(not env.sg.has(RULE), "rule tcp/80 should be revoked after apply")
    s, c, dt = env.call("POST", f"/api/changes/{cid}/confirm")
    check(s == 200 and c.get("status") == "CONFIRMED" and c.get("idempotent") is False, f"confirm: {s} {c}")
    env.sleep(j["ttl_seconds"] + SLACK / 3)  # outlive the window: nothing may revert it
    g = get(env, cid)
    check(g["status"] == "CONFIRMED", f"status after window: {g['status']}")
    check(not env.sg.has(RULE), "rule came back after confirm (schedule not neutralised)")
    return f"confirm {dt:.2f}s"


def s2_timeout(env):
    cid, j = new_change(env)
    check(not env.sg.has(RULE), "rule should be revoked after apply")
    g, waited = wait_status(env, cid, {"REVERTED", "PARTIAL_REVERT", "FAILED"}, j["ttl_seconds"] + SLACK)
    check(g["status"] == "REVERTED", f"final status {g['status']} report={g.get('revert_report')}")
    check(g.get("revert_trigger") == "SCHEDULE", f"trigger {g.get('revert_trigger')}")
    check(env.sg.has(RULE), "rule not restored")
    delay = g["reverted_at"] - j["expires_at"]
    check(delay >= -1, f"reverted {-delay}s before expires_at")
    return f"reverted {delay:+.0f}s vs expires_at (spec 1a bound: -1..15 s fire delay)"


def s3_manual(env):
    cid, j = new_change(env)
    s, r, dt = env.call("POST", f"/api/changes/{cid}/revert")
    check(s == 200 and r.get("status") == "REVERTED" and r.get("revert_trigger") == "MANUAL", f"revert: {s} {r}")
    check(r.get("idempotent") is False, "first manual revert must not be idempotent")
    check(env.sg.has(RULE), "rule not restored")
    check(get(env, cid)["status"] == "REVERTED", "GET disagrees with revert response")
    return f"revert {dt:.2f}s"


def s4_third_party(env):
    cid, _ = new_change(env)
    env.sg.add(RULE_8443)                                   # unrelated edit inside the window
    s, r, _ = env.call("POST", f"/api/changes/{cid}/revert")
    check(s == 200 and r.get("status") == "REVERTED", f"revert: {s} {r}")
    check(env.sg.has(RULE), "tcp/80 not restored")
    check(env.sg.has(RULE_8443), "unrelated rule tcp/8443 was destroyed by revert")
    return "unrelated rule survived"


def s5_late_confirm(env):
    cid, j = new_change(env)
    off = env.now() - j["server_time"]                      # local time minus server time
    env.sleep(max(0, j["expires_at"] + off - env.now()))    # wait until server clock reaches expires_at
    s, c, _ = env.call("POST", f"/api/changes/{cid}/confirm")   # first call at/after expiry
    check(s != 200, f"late confirm returned 200: {c}")
    check(s == 409 and err_code(c) in EXPIRED_CODES, f"unexpected {s} {c}")
    seen = err_code(c)
    if seen != "WINDOW_EXPIRED":
        raise Inconclusive(f"got 409 {seen}: revert had already started before the first post-expiry confirm, "
                           "so WINDOW_EXPIRED was not observable; rerun")
    wait_status(env, cid, {"REVERTED"}, SLACK)
    return "409 WINDOW_EXPIRED"


def s6_double_confirm(env):
    cid, _ = new_change(env)
    s1, a, _ = env.call("POST", f"/api/changes/{cid}/confirm")
    s2, b, _ = env.call("POST", f"/api/changes/{cid}/confirm")
    check(s1 == 200 and a.get("idempotent") is False, f"first: {s1} {a}")
    check(s2 == 200 and b.get("idempotent") is True and b.get("status") == "CONFIRMED", f"second: {s2} {b}")
    return "second confirm idempotent"


def s7_race(env):
    rounds = int(os.environ.get("RACE_ROUNDS", "3"))
    outcomes = []
    for i in range(rounds):
        env.sg.reset()
        cid, _ = new_change(env)
        res, bar = {}, threading.Barrier(2)

        def go(name, path):
            bar.wait()
            res[name] = env.call("POST", path)

        ts = [threading.Thread(target=go, args=("confirm", f"/api/changes/{cid}/confirm")),
              threading.Thread(target=go, args=("revert", f"/api/changes/{cid}/revert"))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        (cs, cj, _), (rs, rj, _) = res["confirm"], res["revert"]
        confirm_won, revert_won = cs == 200, rs == 200 and not rj.get("idempotent")
        check(confirm_won != revert_won, f"round {i}: not exactly one winner: confirm {cs} {cj} revert {rs} {rj}")
        final = get(env, cid)["status"]
        if confirm_won:
            check(rs == 409 and final == "CONFIRMED" and not env.sg.has(RULE), f"round {i}: confirm won but {rs} {final}")
        else:
            check(cs == 409 and final == "REVERTED" and env.sg.has(RULE), f"round {i}: revert won but {cs} {final}")
        outcomes.append("C" if confirm_won else "R")
    return f"{rounds} rounds, winners {''.join(outcomes)}"


def s8_unmanaged(env):
    sg_id = env.sg.create_unmanaged_sg()
    try:
        s, j, _ = apply(env, sg_id=sg_id)
        check(s == 403 and err_code(j) == "SG_NOT_MANAGED", f"expected 403 SG_NOT_MANAGED, got {s} {j}")
    finally:
        env.sg.delete_sg(sg_id)
    return "403 SG_NOT_MANAGED"


def s9_busy(env):
    cid, _ = new_change(env)
    s, j, _ = apply(env, ops=[OP_8081])
    check(s == 409 and err_code(j) == "SG_BUSY", f"expected 409 SG_BUSY, got {s} {j}")
    r, _, _ = env.call("POST", f"/api/changes/{cid}/revert")
    check(r == 200, "cleanup revert failed")
    s, j, _ = apply(env, ops=[OP_8081])                    # lock released after terminal state
    check(s == 201, f"apply after release expected 201, got {s} {j}")
    env.call("POST", f"/api/changes/{j['change_id']}/revert")
    return "409 SG_BUSY, lock released after revert"


def s10_ttl_range(env):
    lo, hi = int(os.environ.get("MIN_TTL_SECONDS", "60")), int(os.environ.get("MAX_TTL_SECONDS", "600"))
    for ttl in (lo - 1, hi + 1):
        s, j, _ = apply(env, ttl=ttl)
        check(s == 400 and err_code(j) == "TTL_OUT_OF_RANGE", f"ttl={ttl}: expected 400 TTL_OUT_OF_RANGE, got {s} {j}")
    check(env.sg.has(RULE), "out-of-range request changed the SG")
    return f"rejected ttl {lo - 1} and {hi + 1}"


SCENARIOS = [("S1", "confirm path", s1_confirm), ("S2", "timeout auto-revert", s2_timeout),
             ("S3", "manual revert", s3_manual), ("S4", "third-party edit survives", s4_third_party),
             ("S5", "late confirm -> WINDOW_EXPIRED", s5_late_confirm), ("S6", "double confirm idempotent", s6_double_confirm),
             ("S7", "confirm vs revert race", s7_race), ("S8", "unmanaged SG -> 403", s8_unmanaged),
             ("S9", "SG_BUSY", s9_busy), ("S10", "TTL out of range", s10_ttl_range)]


def cleanup(env):
    """Leave the SG in baseline: try to end any pending change on it, then reset rules."""
    try:
        env.sg.reset()
    except Exception as e:
        print(f"  (cleanup warning: {e})")


# ------------------------------------------------------------------ fixture check
def check_fixtures():
    need = {"create_201.json": ["change_id", "status", "sg_id", "ttl_seconds", "created_at", "expires_at",
                                "server_time", "schedule_name", "delta"],
            "get_pending.json": ["change_id", "status", "expires_at", "server_time", "apply_done", "delta"],
            "confirm_200.json": ["change_id", "status", "confirmed_at", "idempotent"],
            "revert_manual_200.json": ["change_id", "status", "revert_trigger", "reverted_at", "idempotent",
                                       "revert_report"]}
    bad = 0
    for name, keys in need.items():
        p = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "fixtures", name)
        try:
            d = json.load(open(p, encoding="utf-8"))
        except OSError:
            print(f"MISSING {name}")
            bad += 1
            continue
        miss = [k for k in keys if k not in d]
        if miss:
            bad += 1
            print(f"MISMATCH {name}: missing {miss}")
    for p in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "fixtures", "error_*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        if not isinstance(d.get("error"), dict) or "code" not in d["error"]:
            bad += 1
            print(f"MISMATCH {os.path.basename(p)}: error must be {{code, message}} object per §5")
    print("fixtures match spec §5" if not bad else f"{bad} fixture problem(s): report to M2")
    return 1 if bad else 0


def main(argv):
    if "--check-fixtures" in argv:
        return check_fixtures()
    want = [a.upper() for a in argv if a.upper().startswith("S")]
    if os.environ.get("MOCK") == "1":
        env = MockEnv()
    else:
        missing = [k for k in ("BASE_URL", "TOKEN", "SG_ID") if not os.environ.get(k)]
        if missing:
            sys.exit(f"set {', '.join(missing)} (or MOCK=1)")
        env = HttpEnv()
    env.ttl = int(os.environ.get("TTL_SECONDS", os.environ.get("MIN_TTL_SECONDS", "60")))
    print(f"mode={'MOCK (fake, proves nothing about AWS)' if env.mock else 'REAL'} ttl={env.ttl}s")
    results = []
    for sid, name, fn in SCENARIOS:
        if want and sid not in want:
            continue
        cleanup(env)
        t0 = env.now() if env.mock else time.time()
        try:
            detail, status = fn(env), "PASS"
        except Fail as e:
            detail, status = str(e), "FAIL"
        except Inconclusive as e:
            detail, status = str(e), "INCONCLUSIVE"
        except Exception as e:
            detail, status = f"{type(e).__name__}: {e}", "FAIL"
        cleanup(env)
        el = (env.now() if env.mock else time.time()) - t0
        results.append(status)
        print(f"{status:12} {sid:4} {name:32} {el:6.1f}s{' (fake clock)' if env.mock else ''}  {detail}")
    print(f"\n{results.count('PASS')} pass, {results.count('FAIL')} fail, {results.count('INCONCLUSIVE')} inconclusive")
    return 1 if "FAIL" in results else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
