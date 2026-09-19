import json
def handler(event, context):
    return {"statusCode": 200, "body": json.dumps({"status": "PENDING", "expires_at": "2099-12-31T23:59:59Z"})}
