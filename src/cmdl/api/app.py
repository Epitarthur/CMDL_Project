"""Development connectivity checks; product endpoints are implemented in T21."""

from uuid import uuid4

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="CMDL development service", version="0.1.0")


def database_available() -> bool:
    """Use libpq PG* environment variables; never expose connection details."""
    try:
        with psycopg.connect(connect_timeout=2) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
    except psycopg.Error:
        return False


def unavailable(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "5"},
        content={
            "request_id": str(uuid4()),
            "code": "DependencyUnavailable",
            "message": message,
            "retryable": True,
            "issues": [],
        },
    )


@app.get("/health", include_in_schema=False)
def health():
    """Compose infrastructure check, independent of product data readiness."""
    if not database_available():
        return unavailable("Required database is unavailable.")
    return {"status": "healthy", "database": "connected"}


@app.get("/ready", responses={503: {"description": "Application data unavailable"}})
def ready():
    if not database_available():
        return unavailable("Required database is unavailable.")
    return unavailable(
        "Required snapshot and recommendation dependencies are not ready for serving yet."
    )
