"""AWS Lambda entry point (API Gateway HTTP API). Responses are buffered: HTTP APIs cannot stream."""

from mangum import Mangum

from app.main import app

handler = Mangum(app, lifespan="off")
