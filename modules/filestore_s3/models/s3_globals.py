from odoo import tools

import boto3
import botocore
from botocore.exceptions import ClientError

_global_client_config = botocore.config.Config(
    max_pool_connections=75,
)

_global_s3_bucket = False
_global_disk_first = None
_global_cache_domain = None

def get_s3_active():
    access_key = tools.config.get('s3_access_key', None)
    secret_key = tools.config.get('s3_secret_key', None)
    endpoint = tools.config.get('s3_endpoint', None)
    region = tools.config.get('s3_region', None)
    bucket = tools.config.get('s3_bucket', None)

    if (access_key is None or secret_key is None or endpoint is None or region is None or bucket is None):
        return False
    return True

def get_s3_config():
    access_key = tools.config.get('s3_access_key', None)
    secret_key = tools.config.get('s3_secret_key', None)
    endpoint = tools.config.get('s3_endpoint', None)
    region = tools.config.get('s3_region', None)
    bucket = tools.config.get('s3_bucket', None)

    if (access_key is None or secret_key is None or endpoint is None or region is None or bucket is None):
        raise Exception("Unable to connect to S3 bucket. Check your credentials.")
    
    return access_key, secret_key, endpoint, region, bucket

def get_s3_client():

    access_key, secret_key, endpoint, region, bucket = get_s3_config()
    session = boto3.session.Session()

    client = session.client(
        service_name='s3',
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    return client

def _connect_to_S3_bucket():

    access_key, secret_key, endpoint, region, bucket = get_s3_config()
    session = boto3.session.Session()

    s3_conn = session.resource(
        service_name='s3',
        config=_global_client_config,
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )

    # Get bucket or create one
    s3_bucket = s3_conn.Bucket(bucket)
    exists = True
    try:
        s3_conn.meta.client.head_bucket(Bucket=bucket)
    except botocore.exceptions.ClientError as e:
        error_code = int(e.response['Error']['Code'])
        if error_code == 404:
            exists = False

    if not exists:
        s3_bucket.create_bucket(Bucket=bucket)

    return s3_bucket

def get_s3_bucket():
    global _global_s3_bucket
    if not _global_s3_bucket:
        _global_s3_bucket = _connect_to_S3_bucket()
    return _global_s3_bucket

def get_disk_first():
    global _global_disk_first
    if _global_disk_first is None:
        _global_disk_first = tools.config.get('s3_disk_first', False)
    return _global_disk_first

def get_cache_domain():
    global _global_cache_domain
    if _global_cache_domain is None:
        _global_cache_domain = tools.config.get('cache_domain', False)
    return _global_cache_domain
