"""Shared structured logger. Never log request bodies, patient notes, e-mail addresses or phone numbers."""

from aws_lambda_powertools import Logger

logger = Logger(service="medical-cabinet")
