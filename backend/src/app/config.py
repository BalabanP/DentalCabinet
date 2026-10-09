import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Config:
    users_table: str
    patients_table: str
    appointments_table: str
    settings_table: str
    notifications_table: str
    queue_url: str
    queue_arn: str
    scheduler_group: str
    scheduler_role_arn: str
    ses_from_address: str


@lru_cache
def get_config() -> Config:
    e = os.environ
    return Config(
        users_table=e["USERS_TABLE"],
        patients_table=e["PATIENTS_TABLE"],
        appointments_table=e["APPOINTMENTS_TABLE"],
        settings_table=e["SETTINGS_TABLE"],
        notifications_table=e["NOTIFICATIONS_TABLE"],
        queue_url=e["REMINDER_QUEUE_URL"],
        queue_arn=e["REMINDER_QUEUE_ARN"],
        scheduler_group=e["SCHEDULER_GROUP"],
        scheduler_role_arn=e["SCHEDULER_ROLE_ARN"],
        ses_from_address=e.get("SES_FROM_ADDRESS", ""),
    )
