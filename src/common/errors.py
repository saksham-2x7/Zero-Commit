class DeadmanError(Exception):
    def __init__(self, http_status, code, message, change_id=None, status=None):
        self.http_status = http_status
        self.code = code
        self.message = message
        self.change_id = change_id
        self.status = status
    
    def to_dict(self):
        d = {"code": self.code, "message": self.message}
        if self.change_id: d["change_id"] = self.change_id
        if self.status: d["status"] = self.status
        return {"error": d}

def get_error(code, message, change_id=None, status=None):
    mapping = {
        "INVALID_REQUEST": 400,
        "TTL_OUT_OF_RANGE": 400,
        "UNSUPPORTED_RULE": 400,
        "UNAUTHORIZED": 403,  # API Gateway uses 403 for both 401/403
        "SG_NOT_MANAGED": 403,
        "SG_NOT_FOUND": 404,
        "CHANGE_NOT_FOUND": 404,
        "SG_BUSY": 409,
        "RULE_ALREADY_EXISTS": 409,
        "RULE_NOT_FOUND": 409,
        "NOT_APPLIED_YET": 409,
        "WINDOW_EXPIRED": 409,
        "CHANGE_NOT_PENDING": 409,
        "REVERT_IN_PROGRESS": 409,
        "SCHEDULE_FAILED": 502,
        "APPLY_FAILED_REVERTED": 500,
        "APPLY_FAILED_REVERT_INCOMPLETE": 500,
        "INTERNAL": 500
    }
    return DeadmanError(mapping.get(code, 500), code, message, change_id, status)
