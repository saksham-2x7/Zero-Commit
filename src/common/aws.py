import boto3
import os

def get_ec2_client():
    return boto3.client('ec2', region_name=os.environ.get("AWS_REGION", "ap-south-1"))

def get_scheduler_client():
    return boto3.client('scheduler', region_name=os.environ.get("AWS_REGION", "ap-south-1"))
