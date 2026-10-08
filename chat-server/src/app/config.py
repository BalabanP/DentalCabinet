import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# chat-server/.env for local runs. It is outside src/, so it is never packaged into the Lambda.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    api_base_url: str
    enable_patient_notes: bool
    max_tool_rounds: int = 8
    max_messages: int = 100


@lru_cache
def _secret_value(secret_id: str) -> str:
    """Read the LLM key from Secrets Manager. Empty string while no value has been stored yet."""
    import boto3
    from botocore.exceptions import ClientError

    try:
        return boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"].strip()
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("ResourceNotFoundException", "InvalidRequestException"):
            return ""
        raise


def load_config() -> Config:
    e = os.environ
    api_key = e.get("ANTHROPIC_API_KEY") or e.get("LLM_API_KEY", "")
    if not api_key and e.get("LLM_API_KEY_SECRET_ARN"):
        api_key = _secret_value(e["LLM_API_KEY_SECRET_ARN"])
    return Config(
        api_key=api_key,
        base_url=e.get("LLM_BASE_URL", "https://api.anthropic.com/v1/"),
        model=e.get("LLM_MODEL", "claude-haiku-4-5-20251001"),
        api_base_url=e.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        enable_patient_notes=e.get("ENABLE_PATIENT_NOTES", "true").lower() != "false",
    )
