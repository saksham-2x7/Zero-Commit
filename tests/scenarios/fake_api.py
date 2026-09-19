"""In-process fake of the Deadman API for MOCK=1, following MASTER_SPEC §4/§5.

It exists to debug the scenario runner. Passing against it proves nothing about the real system.
docs/fixtures/*.json do not yet match spec §5 in several files (see `run.py --check-fixtures`),
so they are not used to build responses.
"""
import threading

MIN_TTL, MAX_TTL = 60, 600
SCHEDULER_DELAY = 3          # fake scheduler fires this many seconds after expires_at
BASELINE = ("tcp", 80, 80, "0.0.0.0/0")


class FakeClock:
    def __init__(self):
        self.t = 1_758_300_000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeSG:
    """Same interface as sg_helpers.RealSG."""

    def __init__(self, sg_id="sg-mock"):
        self.sg_id, self._rules = sg_id, {BASELINE}

    def rules(self):
        return set(self._rules)

    def has(self, r):
        return r in self._rules

    def add(self, r):
        self._rules.add(r)

    def remove(self, r):
        self._rules.discard(r)

    def reset(self):
        self._rules = {BASELINE}

    def create_unmanaged_sg(self):
        return "sg-unmanaged"

    def delete_sg(self, sg_id):
        pass


class FakeAPI:
    def __init__(self, clock, sg, token="mock"):
        self.clock, self.sg, self.token = clock, sg, token
        self.changes, self.lock, self.mu, self.n = {}, {}, threading.Lock(), 0

    # ---- helpers
    @staticmethod
    def err(http, code, msg="", **extra):
        return http, {"error": {"code": code, "message": msg or code, **extra}}

    def _tick(self):
        now = self.clock.now()
        for c in self.changes.values():
            if c["status"] == "PENDING" and now >= c["expires_at"] + SCHEDULER_DELAY:
                self._do_revert(c, "SCHEDULE")

    def _do_revert(self, c, trigger):
        report = []
        for op in reversed(c["delta"]):
            t = (op["protocol"], op["from_port"], op["to_port"], op["cidr"])
            if op["action"] == "REVOKE":
                self.sg.add(t)
            else:
                self.sg.remove(t)
            report.append({"op_id": op["op_id"], "result": "REVERTED", "detail": ""})
        c.update(status="REVERTED", revert_trigger=trigger, reverted_at=int(self.clock.now()), revert_report=report)
        self.lock.pop(c["sg_id"], None)

    # ---- entry point
    def request(self, method, path, body=None, auth=True):
        with self.mu:
            if not auth:
                return self.err(401, "UNAUTHORIZED")
            self._tick()
            parts = [p for p in path.split("/") if p]  # api, changes, [id, [action]]
            if method == "POST" and parts == ["api", "changes"]:
                return self._apply(body or {})
            if len(parts) >= 3 and parts[:2] == ["api", "changes"]:
                c = self.changes.get(parts[2])
                if c is None:
                    return self.err(404, "CHANGE_NOT_FOUND")
                if method == "GET" and len(parts) == 3:
                    return 200, self._view(c)
                if method == "POST" and parts[3:] == ["confirm"]:
                    return self._confirm(c)
                if method == "POST" and parts[3:] == ["revert"]:
                    return self._revert(c)
            return self.err(404, "NOT_FOUND")

    def _view(self, c):
        d = {k: c.get(k) for k in ("change_id", "status", "sg_id", "ttl_seconds", "created_at", "expires_at",
                                   "apply_done", "delta", "confirmed_at", "reverted_at", "revert_trigger",
                                   "revert_report", "failure_reason")}
        d["server_time"] = int(self.clock.now())
        return d

    def _apply(self, b):
        ops, ttl, sg_id = b.get("ops"), b.get("ttl_seconds"), b.get("sg_id")
        if not isinstance(ops, list) or not 1 <= len(ops) <= 5 or not isinstance(ttl, int) or not sg_id:
            return self.err(400, "INVALID_REQUEST")
        if not MIN_TTL <= ttl <= MAX_TTL:
            return self.err(400, "TTL_OUT_OF_RANGE")
        if sg_id == "sg-unmanaged":
            return self.err(403, "SG_NOT_MANAGED")
        if sg_id != self.sg.sg_id:
            return self.err(404, "SG_NOT_FOUND")
        if sg_id in self.lock:
            return self.err(409, "SG_BUSY", change_id=self.lock[sg_id])
        delta = []
        for i, o in enumerate(ops, 1):
            t = (o["protocol"], o["from_port"], o["to_port"], o["cidr"])
            if o["action"] == "REVOKE" and not self.sg.has(t):
                return self.err(409, "RULE_NOT_FOUND")
            if o["action"] == "AUTHORIZE" and self.sg.has(t):
                return self.err(409, "RULE_ALREADY_EXISTS")
            delta.append({"op_id": i, **o, "applied": None})
        self.n += 1
        now = int(self.clock.now())
        c = {"change_id": f"01FAKE{self.n:04d}", "status": "PENDING", "sg_id": sg_id, "ttl_seconds": ttl,
             "created_at": now, "expires_at": now + ttl, "apply_done": True, "delta": delta, "confirmed_at": None,
             "reverted_at": None, "revert_trigger": None, "revert_report": None, "failure_reason": None}
        for op in delta:
            t = (op["protocol"], op["from_port"], op["to_port"], op["cidr"])
            (self.sg.remove if op["action"] == "REVOKE" else self.sg.add)(t)
            op["applied"] = True
        self.changes[c["change_id"]] = c
        self.lock[sg_id] = c["change_id"]
        return 201, {**self._view(c), "schedule_name": f"dm-{c['change_id']}"}

    def _confirm(self, c):
        if c["status"] == "CONFIRMED":
            return 200, {"change_id": c["change_id"], "status": "CONFIRMED", "confirmed_at": c["confirmed_at"],
                         "idempotent": True}
        if c["status"] != "PENDING":
            return self.err(409, "CHANGE_NOT_PENDING", status=c["status"])
        if self.clock.now() >= c["expires_at"]:
            return self.err(409, "WINDOW_EXPIRED")
        c.update(status="CONFIRMED", confirmed_at=int(self.clock.now()))
        self.lock.pop(c["sg_id"], None)
        return 200, {"change_id": c["change_id"], "status": "CONFIRMED", "confirmed_at": c["confirmed_at"],
                     "idempotent": False}

    def _revert(self, c):
        if c["status"] == "REVERTED":
            return 200, {**self._resp_revert(c), "idempotent": True}
        if c["status"] != "PENDING":
            return self.err(409, "CHANGE_NOT_PENDING", status=c["status"])
        self._do_revert(c, "MANUAL")
        return 200, {**self._resp_revert(c), "idempotent": False}

    @staticmethod
    def _resp_revert(c):
        return {k: c[k] for k in ("change_id", "status", "revert_trigger", "reverted_at", "revert_report")}
