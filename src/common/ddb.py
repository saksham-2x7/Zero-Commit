import boto3
import os
import json
from decimal import Decimal

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj)
        return super(DecimalEncoder, self).default(obj)

def get_table():
    dynamodb = boto3.resource('dynamodb')
    table_name = os.environ.get("TABLE_NAME")
    return dynamodb.Table(table_name)
