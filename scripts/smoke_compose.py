"""Verify Compose in a disposable project, including persistence and explicit volume reset.

Run with `uv run python scripts/smoke_compose.py`. Only this script's unique project
is removed. Never points to the normal development project's volume.
"""

import os
import socket
import subprocess
from uuid import uuid4

import httpx


def free_port() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return str(sock.getsockname()[1])


def main() -> None:
    project = f"cmdl-smoke-{uuid4().hex[:12]}"
    env = {
        **os.environ,
        "API_PORT": free_port(),
        "UI_PORT": free_port(),
        "COMPOSE_PROFILES": "",
        "POSTGRES_DB": "cmdl",
        "POSTGRES_USER": "cmdl",
        "POSTGRES_PASSWORD": "cmdl-smoke-only",
    }
    base = ["docker", "compose", "-f", "compose.yaml", "--env-file", ".env.example", "-p", project]

    def compose(*args: str) -> str:
        result = subprocess.run(
            [*base, *args], env=env, check=True, text=True, stdout=subprocess.PIPE
        )
        return result.stdout.strip()

    def sql(query: str) -> str:
        return compose("exec", "-T", "db", "psql", "-U", "cmdl", "-d", "cmdl", "-Atc", query)

    def up() -> None:
        compose("up", "-d", "--wait", "--wait-timeout", "120")

    try:
        compose("build")
        up()
        assert "0001_storage_v1.sql" in compose(
            "--profile", "jobs", "run", "--rm", "job", "cmdl", "migrate"
        )
        assert (
            compose("--profile", "jobs", "run", "--rm", "job", "cmdl", "migrate")
            == "Database is up to date."
        )
        assert sql("SELECT count(*) FROM schema_migrations") == "2"
        assert compose(
            "--profile",
            "jobs",
            "run",
            "--rm",
            "job",
            "cmdl",
            "ingest",
            "--output-dir",
            "/tmp/cmdl-import-smoke",
        ).startswith("Imported:")
        assert compose(
            "--profile",
            "jobs",
            "run",
            "--rm",
            "job",
            "cmdl",
            "ingest",
            "--output-dir",
            "/tmp/cmdl-import-smoke",
        ).startswith("Already imported:")
        assert sql("SELECT count(*) FROM source_rows") == "75434"
        assert sql("SELECT count(*) FROM source_imports") == "1"
        import_id = sql("SELECT import_id FROM source_imports")
        assert compose(
            "--profile",
            "jobs",
            "run",
            "--rm",
            "job",
            "cmdl",
            "snapshot",
            "--import-id",
            import_id,
            "--output-dir",
            "/tmp/cmdl-snapshots-smoke",
        ).startswith("Published benchmark snapshots.")
        assert compose(
            "--profile",
            "jobs",
            "run",
            "--rm",
            "job",
            "cmdl",
            "snapshot",
            "--import-id",
            import_id,
            "--output-dir",
            "/tmp/cmdl-snapshots-smoke",
        ).startswith("Already published benchmark snapshots.")
        assert sql("SELECT count(*) FROM snapshots WHERE status='published'") == "2"
        with httpx.Client(timeout=10) as client:
            response = client.get(f"http://127.0.0.1:{env['API_PORT']}/health")
            response.raise_for_status()
            assert response.json()["database"] == "connected"
            assert client.get(f"http://127.0.0.1:{env['API_PORT']}/ready").status_code == 503
            client.get(f"http://127.0.0.1:{env['UI_PORT']}/_stcore/health").raise_for_status()
        # Exercise the packaged reader examples; product API routes remain T21.
        compose(
            "exec",
            "-T",
            "ui",
            "python",
            "-c",
            "from streamlit.testing.v1 import AppTest; "
            "a=AppTest.from_file('src/cmdl/ui/app.py').run(timeout=20); "
            "assert not a.exception; assert not a.error; "
            "a.button[0].click().run(timeout=20); "
            "assert not a.exception; assert not a.error; "
            "assert any(s.value == 'Your reading list' for s in a.subheader)",
        )
        compose(
            "--profile",
            "jobs",
            "run",
            "--rm",
            "job",
            "cmdl",
            "profile",
            "--output-dir",
            "/tmp/cmdl-profile-smoke",
        )
        sql("CREATE TABLE t05_probe(value TEXT); INSERT INTO t05_probe VALUES ('persisted');")
        compose("restart", "db")
        up()
        assert sql("SELECT value FROM t05_probe") == "persisted"
        compose("down")
        up()
        assert sql("SELECT value FROM t05_probe") == "persisted"
        assert sql("SELECT count(*) FROM schema_migrations") == "2"
        assert sql("SELECT count(*) FROM source_imports") == "1"
        assert sql("SELECT count(*) FROM snapshots WHERE status='published'") == "2"
        compose("down", "--volumes")
        up()
        assert sql("SELECT to_regclass('public.t05_probe') IS NULL") == "t"
        assert sql("SELECT to_regclass('public.schema_migrations') IS NULL") == "t"
        assert "0001_storage_v1.sql" in compose(
            "--profile", "jobs", "run", "--rm", "job", "cmdl", "migrate"
        )
        assert sql("SELECT count(*) FROM source_imports") == "0"
        assert sql("SELECT count(*) FROM snapshots") == "0"
        print("PASS: UI → API → DB, migrations, import/snapshot replay, persistence and reset")
    except Exception:
        print(compose("logs", "--tail=100", "db", "api", "ui"))
        raise
    finally:
        compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
