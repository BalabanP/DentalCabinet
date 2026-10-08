import os
import sys
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.update(
    AWS_DEFAULT_REGION="eu-central-1",
    AWS_ACCESS_KEY_ID="test",
    AWS_SECRET_ACCESS_KEY="test",
    POWERTOOLS_LOG_LEVEL="WARNING",
)

TABLES = {
    "USERS_TABLE": ("users", "user_id", None, [("email-index", "email", None)]),
    "PATIENTS_TABLE": ("patients", "user_id", None, []),
    "APPOINTMENTS_TABLE": (
        "appointments",
        "appointment_id",
        None,
        [("user-index", "user_id", "start_at"), ("day-index", "day", "start_at")],
    ),
    "SETTINGS_TABLE": ("settings", "setting_id", None, []),
    "NOTIFICATIONS_TABLE": ("notifications", "appointment_id", "sk", []),
}


def _create_table(ddb, name, hash_key, range_key, gsis):
    attrs = {hash_key} | ({range_key} if range_key else set())
    schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    if range_key:
        schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
    index_defs = []
    for idx_name, idx_hash, idx_range in gsis:
        attrs |= {idx_hash} | ({idx_range} if idx_range else set())
        idx_schema = [{"AttributeName": idx_hash, "KeyType": "HASH"}]
        if idx_range:
            idx_schema.append({"AttributeName": idx_range, "KeyType": "RANGE"})
        index_defs.append(
            {"IndexName": idx_name, "KeySchema": idx_schema, "Projection": {"ProjectionType": "ALL"}}
        )
    kwargs = dict(
        TableName=name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in sorted(attrs)],
        KeySchema=schema,
    )
    if index_defs:
        kwargs["GlobalSecondaryIndexes"] = index_defs
    ddb.create_table(**kwargs)


@pytest.fixture
def aws(monkeypatch):
    from app import clients
    from app.config import get_config

    with mock_aws():
        ddb = boto3.client("dynamodb")
        for env_name, (name, hash_key, range_key, gsis) in TABLES.items():
            _create_table(ddb, name, hash_key, range_key, gsis)
            monkeypatch.setenv(env_name, name)

        sqs = boto3.client("sqs")
        queue_url = sqs.create_queue(QueueName="reminders")["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        boto3.client("scheduler").create_schedule_group(Name="reminders")

        monkeypatch.setenv("REMINDER_QUEUE_URL", queue_url)
        monkeypatch.setenv("REMINDER_QUEUE_ARN", queue_arn)
        monkeypatch.setenv("SCHEDULER_GROUP", "reminders")
        monkeypatch.setenv("SCHEDULER_ROLE_ARN", "arn:aws:iam::123456789012:role/scheduler")
        monkeypatch.setenv("SES_FROM_ADDRESS", "clinic@example.com")
        monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-number-id-123")

        get_config.cache_clear()
        clients.reset_clients()
        yield SimpleNamespace(sqs=sqs, queue_url=queue_url, scheduler=boto3.client("scheduler"))
        get_config.cache_clear()
        clients.reset_clients()


def queued_messages(aws) -> list[dict]:
    import json

    out = []
    while True:
        res = aws.sqs.receive_message(QueueUrl=aws.queue_url, MaxNumberOfMessages=10)
        if "Messages" not in res:
            return out
        for m in res["Messages"]:
            out.append(json.loads(m["Body"]))
            aws.sqs.delete_message(QueueUrl=aws.queue_url, ReceiptHandle=m["ReceiptHandle"])


def schedules(aws) -> list[str]:
    res = aws.scheduler.list_schedules(GroupName="reminders")
    return sorted(s["Name"] for s in res["Schedules"])
