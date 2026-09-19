import json, uuid, os
def handler(event, context):
    return {"statusCode": 201, "body": json.dumps({"id": str(uuid.uuid4()), "status": "PENDING", "expires_at": "2099-12-31T23:59:59Z"})}
