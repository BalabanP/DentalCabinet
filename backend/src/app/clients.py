from functools import lru_cache

import boto3


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


def reset_clients() -> None:
    """Drop cached clients (used by tests that swap in mocked AWS)."""
    for fn in (_dynamodb, sqs, scheduler, sesv2, ssm):
        fn.cache_clear()
