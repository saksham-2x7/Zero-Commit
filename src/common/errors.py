"""
Deadman error definitions and HTTP mapping.
Spec §5 API Contract.
"""
from typing import Optional, Dict, Any
import json

ERROR_STATUS_CODES: Dict[str, int] = {
    "INVALID_REQUEST": 400,
    "TTL_OUT_OF_RANGE": 400,
    "UNSUPPORTED_RULE": 400,
    "UNAUTHORIZED": 401,
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
    "INTERNAL": 500,
}


class DeadmanError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        change_id: Optional[str] = None,
        status: Optional[str] = None,
        status_code: Optional[int] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.change_id = change_id
        self.status = status
        self.status_code = status_code if status_code is not None else ERROR_STATUS_CODES.get(code, 500)

    def to_dict(self) -> Dict[str, Any]:
        err: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.change_id is not None:
            err["change_id"] = self.change_id
        if self.status is not None:
            err["status"] = self.status
        return {"error": err}

    def to_response(self) -> Dict[str, Any]:
        return {
            "statusCode": self.status_code,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(self.to_dict()),
        }


def make_error_response(
    code: str,
    message: str,
    change_id: Optional[str] = None,
    status: Optional[str] = None,
    status_code: Optional[int] = None,
) -> Dict[str, Any]:
    err = DeadmanError(code, message, change_id=change_id, status=status, status_code=status_code)
    return err.to_response()
