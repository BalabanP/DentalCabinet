import os
from functools import lru_cache

import boto3
from botocore.config import Config


@lru_cache
def _dynamodb():
    return boto3.resource("dynamodb")


def table(name: str):
    return _dynamodb().Table(name)


@lru_cache
def sqs():
    return boto3.client("sqs")


@lru_cache
def scheduler():
    return boto3.client("scheduler")


@lru_cache
def sesv2():
    return boto3.client("sesv2")


@lru_cache
def ssm():
    return boto3.client("ssm")


@lru_cache
def s3():
    # Regional endpoint + SigV4: links handed to the browser (upload/download) must be signed for the
    # bucket's own region, or S3 answers with a redirect the browser cannot follow for a signed request.
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "eu-central-1"
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def reset_clients() -> None:
    """Drop cached clients (used by tests that swap in mocked AWS)."""
    for fn in (_dynamodb, sqs, scheduler, sesv2, ssm, s3):
        fn.cache_clear()
