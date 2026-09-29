"""Fixtures: a real PostgreSQL (PostGIS + pgvector) and a real RabbitMQ broker.

Nothing here is mocked. What is under test -- extensions, constraints, dead-letter routing --
lives inside Postgres and RabbitMQ, so a fake of either would only test the fake.

By default each server is started in a container (Docker must be running); the Postgres
image is built from infra/postgres/Dockerfile, the same one docker compose builds. To
point the suite at servers you already run instead, set:

    GRIDLOCK_TEST_DATABASE_URL    a superuser DSN, e.g. postgresql://postgres@localhost:5432/postgres
                                  (then migrate.sh runs locally, so sh and psql must be on PATH)
    GRIDLOCK_TEST_AMQP_URL        e.g. amqp://guest:guest@localhost:5672/%2F
    GRIDLOCK_TEST_RABBITMQ_API    e.g. http://guest:guest@localhost:15672

If neither the variables nor Docker are available the suite errors -- it does not skip.
A check that did not run is a failed check.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import LiteralString, cast

import pika
import psycopg
import pytest
from pika.adapters.blocking_connection import BlockingChannel
from psycopg import sql
from testcontainers.core.container import DockerContainer
from testcontainers.core.image import DockerImage
from testcontainers.core.wait_strategies import HttpWaitStrategy, LogMessageWaitStrategy

INFRA = Path(__file__).resolve().parent.parent
POSTGRES_DIR = INFRA / "postgres"
INIT_SQL = POSTGRES_DIR / "init.sql"
MIGRATIONS = POSTGRES_DIR / "migrations"
MIGRATE_SH = POSTGRES_DIR / "migrate.sh"
DEFINITIONS = INFRA / "rabbitmq" / "definitions.json"

# Kept in step with docker-compose.yml, so the suite tests what `compose up` runs.
POSTGRES_TEST_TAG = "gridlock/postgres:test"
RABBITMQ_IMAGE = "rabbitmq:4.1-management"


# --------------------------------------------------------------- Postgres ---


def _wait_for_postgres(dsn: str, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=3):
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@dataclass(frozen=True)
class MigrateRun:
    code: int
    output: str


# Run migrate.sh against the named database. `extra` adds migration files (name -> SQL)
# to a copy of infra/postgres/migrations, so a test can try one that fails.
Migrate = Callable[[str, dict[str, str] | None], MigrateRun]


@dataclass(frozen=True)
class PostgresServer:
    dsn: str  # superuser, database `postgres`
    run_migrate: Migrate

    def migrate(self, dbname: str, extra: dict[str, str] | None = None) -> MigrateRun:
        return self.run_migrate(dbname, extra)


def _migrate_with_local_psql(dsn: str) -> Migrate:
    """Run migrate.sh here, against an external server. Needs sh and psql on PATH."""
    parts = urllib.parse.urlsplit(dsn)

    def run(dbname: str, extra: dict[str, str] | None) -> MigrateRun:
        with tempfile.TemporaryDirectory() as tmp:
            migrations = MIGRATIONS
            if extra:
                migrations = Path(tmp)
                for existing in MIGRATIONS.glob("*.sql"):
                    shutil.copy(existing, migrations)
                for name, body in extra.items():
                    (migrations / name).write_text(body, encoding="utf-8")
            env = {
                **os.environ,
                "PGHOST": parts.hostname or "localhost",
                "PGPORT": str(parts.port or 5432),
                "PGUSER": urllib.parse.unquote(parts.username or "postgres"),
                "PGPASSWORD": urllib.parse.unquote(parts.password or ""),
                "PGDATABASE": dbname,
                "MIGRATIONS_DIR": str(migrations),
            }
            done = subprocess.run(
                ["sh", str(MIGRATE_SH)], env=env, capture_output=True, text=True, check=False
            )
        return MigrateRun(done.returncode, done.stdout + done.stderr)

    return run


def _migrate_in_container(container: DockerContainer) -> Migrate:
    """Run migrate.sh inside the Postgres container, as the compose db-migrate job does."""

    def sh(script: str, *env: str) -> MigrateRun:
        code, raw = container.exec(["env", *env, "sh", "-c", script])
        # exec() returns the whole output as bytes unless asked to stream it.
        output = raw.decode(errors="replace") if isinstance(raw, bytes) else ""
        return MigrateRun(-1 if code is None else code, output)  # None: no exit code = failed

    def run(dbname: str, extra: dict[str, str] | None) -> MigrateRun:
        migrations = "/migrations"
        if extra:
            migrations = f"/tmp/migrations-{uuid.uuid4().hex[:8]}"
            copy = [f"mkdir -p {migrations}", f"cp /migrations/*.sql {migrations}/"]
            for name, body in extra.items():
                encoded = base64.b64encode(body.encode()).decode()
                copy.append(f"echo {encoded} | base64 -d > {migrations}/{name}")
            prepared = sh(" && ".join(copy))
            assert prepared.code == 0, prepared.output
        return sh(
            "sh /migrate.sh",
            "PGUSER=postgres",
            f"PGDATABASE={dbname}",
            f"MIGRATIONS_DIR={migrations}",
        )

    return run


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[PostgresServer]:
    """A PostGIS + pgvector server the suite may create databases on."""
    external = os.environ.get("GRIDLOCK_TEST_DATABASE_URL")
    if external:
        yield PostgresServer(external, _migrate_with_local_psql(external))
        return

    with DockerImage(path=POSTGRES_DIR, tag=POSTGRES_TEST_TAG, clean_up=False) as image:
        container = (
            DockerContainer(str(image))
            .with_env("POSTGRES_PASSWORD", "gridlock-test")
            .with_exposed_ports(5432)
            .with_volume_mapping(str(MIGRATIONS), "/migrations", "ro")
            .with_volume_mapping(str(MIGRATE_SH), "/migrate.sh", "ro")
            # The first "ready" line can come from the image's temporary init server, which
            # listens on its socket only; _wait_for_postgres below then waits for TCP, which
            # only the real server answers.
            .waiting_for(LogMessageWaitStrategy("database system is ready to accept connections"))
        )
        with container:
            host = container.get_container_host_ip()
            port = container.get_exposed_port(5432)
            dsn = f"postgresql://postgres:gridlock-test@{host}:{port}/postgres"
            _wait_for_postgres(dsn)
            yield PostgresServer(dsn, _migrate_in_container(container))


def _with_dbname(dsn: str, dbname: str) -> str:
    parts = urllib.parse.urlsplit(dsn)
    return urllib.parse.urlunsplit(parts._replace(path=f"/{dbname}"))


@contextlib.contextmanager
def fresh_database(server: PostgresServer) -> Iterator[str]:
    """A new, empty database with init.sql applied (what initdb does under compose).
    Dropped afterwards. Returns its DSN."""
    dbname = f"gridlock_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(server.dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    dsn = _with_dbname(server.dsn, dbname)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            # Committed SQL, run as written -- as Postgres' initdb does with it.
            conn.execute(cast(LiteralString, INIT_SQL.read_text(encoding="utf-8")))
        yield dsn
    finally:
        with psycopg.connect(server.dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
            )


def dbname_of(dsn: str) -> str:
    return urllib.parse.urlsplit(dsn).path.lstrip("/")


@pytest.fixture(scope="session")
def migrated_db(postgres_server: PostgresServer) -> Iterator[str]:
    """A fresh database set up exactly as compose sets one up: init.sql, then migrate.sh."""
    with fresh_database(postgres_server) as dsn:
        run = postgres_server.migrate(dbname_of(dsn))
        assert run.code == 0, f"migrate.sh failed ({run.code}):\n{run.output}"
        yield dsn


@pytest.fixture
def db(migrated_db: str) -> Iterator[psycopg.Connection]:
    """A connection whose work is rolled back after the test, so tests cannot leak rows."""
    conn = psycopg.connect(migrated_db)
    # Open the outer transaction now. Without it, the first `conn.transaction()` in a
    # test would start a real transaction and COMMIT on exit instead of a savepoint.
    conn.execute("SELECT 1")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


# --------------------------------------------------------------- RabbitMQ ---


@dataclass(frozen=True)
class Broker:
    amqp_url: str
    api_url: str  # http://host:port, credentials held separately
    user: str
    password: str

    def api(self, method: str, path: str, body: bytes | None = None) -> object:
        """Call the management HTTP API and return the decoded JSON (or None)."""
        req = urllib.request.Request(f"{self.api_url}{path}", data=body, method=method)
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def load_definitions(self) -> None:
        """Import definitions.json exactly as docker-compose's rabbitmq-topology job does."""
        self.api("POST", "/api/definitions", DEFINITIONS.read_bytes())


def _split_api_url(url: str) -> tuple[str, str, str]:
    parts = urllib.parse.urlsplit(url)
    base = urllib.parse.urlunsplit(
        parts._replace(netloc=f"{parts.hostname}:{parts.port}", path="", query="", fragment="")
    )
    return (
        base,
        urllib.parse.unquote(parts.username or ""),
        urllib.parse.unquote(parts.password or ""),
    )


@pytest.fixture(scope="session")
def broker() -> Iterator[Broker]:
    """A broker with the GridLock topology loaded."""
    amqp = os.environ.get("GRIDLOCK_TEST_AMQP_URL")
    api = os.environ.get("GRIDLOCK_TEST_RABBITMQ_API")
    if amqp and api:
        base, user, password = _split_api_url(api)
        b = Broker(amqp, base, user, password)
        b.load_definitions()
        yield b
        return
    if amqp or api:
        pytest.fail("set both GRIDLOCK_TEST_AMQP_URL and GRIDLOCK_TEST_RABBITMQ_API, or neither")

    user, password = "gridlock", "gridlock-test"
    container = (
        DockerContainer(RABBITMQ_IMAGE)
        .with_env("RABBITMQ_DEFAULT_USER", user)
        .with_env("RABBITMQ_DEFAULT_PASS", password)
        .with_exposed_ports(5672, 15672)
        .waiting_for(
            HttpWaitStrategy(15672, "/api/overview")
            .with_basic_credentials(user, password)
            .for_status_code(200)
            .with_startup_timeout(120)
        )
    )
    with container:
        host = container.get_container_host_ip()
        amqp_port = container.get_exposed_port(5672)
        api_port = container.get_exposed_port(15672)
        b = Broker(
            f"amqp://{user}:{password}@{host}:{amqp_port}/%2F",
            f"http://{host}:{api_port}",
            user,
            password,
        )
        b.load_definitions()
        yield b


@pytest.fixture
def channel(broker: Broker) -> Iterator[BlockingChannel]:
    """A channel on a broker whose GridLock queues start empty."""
    connection = pika.BlockingConnection(pika.URLParameters(broker.amqp_url))
    ch = connection.channel()
    for queue in ("triage.report_received", "verifier.report_triaged", "gridlock.dlq"):
        ch.queue_purge(queue)
    try:
        yield ch
    finally:
        if connection.is_open:
            connection.close()
