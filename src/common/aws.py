# src/common/aws.py
import boto3

_ec2_client = None
_ddb_client = None
_scheduler_client = None

def get_ec2_client():
    global _ec2_client
    if _ec2_client is None:
        _ec2_client = boto3.client('ec2')
    return _ec2_client

def get_ddb_client():
    global _ddb_client
    if _ddb_client is None:
        _ddb_client = boto3.client('dynamodb')
    return _ddb_client

def get_scheduler_client():
    global _scheduler_client
    if _scheduler_client is None:
        _scheduler_client = boto3.client('scheduler')
    return _scheduler_client

# For testing injection
def set_ec2_client(client):
    global _ec2_client
    _ec2_client = client

def set_ddb_client(client):
    global _ddb_client
    _ddb_client = client

def set_scheduler_client(client):
    global _scheduler_client
    _scheduler_client = client
