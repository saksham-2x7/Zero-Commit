# src/common/errors.py

ERROR_MAPPING = {
    "UNAUTHORIZED": 401,
    "SG_NOT_MANAGED": 403,
    "NOT_FOUND": 404,
    "WINDOW_EXPIRED": 409,
    "SG_BUSY": 409,
    "BAD_REQUEST": 400,
    "INTERNAL_ERROR": 500
}

class APIError(Exception):
    def __init__(self, code, message=""):
        self.code = code
        self.http_status = ERROR_MAPPING.get(code, 500)
        self.message = message
        super().__init__(self.message)
