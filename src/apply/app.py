import json

def handler(event, context):
    return {
        "statusCode": 501,
        "body": json.dumps({"message": "Not Implemented"})
    }
