"""Disposable PostgreSQL schemas for integration checks; CI always supplies the DSN."""

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


@pytest.fixture
def empty_db():
    dsn = os.environ.get("CMDL_TEST_DSN")
    if not dsn:
        pytest.skip("Set CMDL_TEST_DSN for real PostgreSQL migration/constraint checks")
    schema = "cmdl_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    with psycopg.connect(dsn, options=f"-c search_path={schema} -c timezone=UTC") as connection:
        try:
            yield connection
        finally:
            connection.rollback()
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
