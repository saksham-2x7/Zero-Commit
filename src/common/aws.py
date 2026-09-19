"""
AWS client factory with dependency injection support.
"""
import os
from typing import Optional, Any
import boto3

_ec2_client: Optional[Any] = None
_ddb_client: Optional[Any] = None
_scheduler_client: Optional[Any] = None


def get_region() -> str:
    return os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-south-1"))


def get_ec2_client(client: Optional[Any] = None) -> Any:
    global _ec2_client
    if client is not None:
        return client
    if _ec2_client is not None:
        return _ec2_client
    return boto3.client("ec2", region_name=get_region())


def get_dynamodb_client(client: Optional[Any] = None) -> Any:
    global _ddb_client
    if client is not None:
        return client
    if _ddb_client is not None:
        return _ddb_client
    return boto3.client("dynamodb", region_name=get_region())


def get_scheduler_client(client: Optional[Any] = None) -> Any:
    global _scheduler_client
    if client is not None:
        return client
    if _scheduler_client is not None:
        return _scheduler_client
    return boto3.client("scheduler", region_name=get_region())


def set_ec2_client(client: Optional[Any]) -> None:
    global _ec2_client
    _ec2_client = client


def set_dynamodb_client(client: Optional[Any]) -> None:
    global _ddb_client
    _ddb_client = client


def set_scheduler_client(client: Optional[Any]) -> None:
    global _scheduler_client
    _scheduler_client = client


def reset_clients() -> None:
    global _ec2_client, _ddb_client, _scheduler_client
    _ec2_client = None
    _ddb_client = None
    _scheduler_client = None
