# infra — the local stack

PostgreSQL (with PostGIS and pgvector) and RabbitMQ, with the queue topology every GridLock
service builds against, and the tests that check both against a real server.

```
postgres/Dockerfile          PostGIS 17 base image + pgvector
postgres/init.sql            enables the postgis and vector extensions (runs once, on a new volume)
rabbitmq/definitions.json    exchanges, queues, bindings, dead-lettering
tests/                       run against real Postgres + RabbitMQ (no mocks)
```

## Run it

From the repo root:

```bash
cp .env.example .env        # set your own local passwords
docker compose up -d
docker compose ps           # postgres + rabbitmq "healthy"; rabbitmq-topology "exited (0)"
```

- **Postgres** listens on `localhost:5432`, database `gridlock`, with `postgis` and `vector`
  enabled. The first `up` builds the image (`gridlock/postgres:17-3.5-pgvector`), which takes a
  minute. `init.sql` runs only when the data volume is first created. To start again from
  nothing, run `docker compose down -v && docker compose up -d`.
- **RabbitMQ** listens on `localhost:5672`, with the management UI at <http://localhost:15672>
  (log in with the user and password from your `.env`). Once the broker is healthy, the
  `rabbitmq-topology` job loads `definitions.json` through the management API and exits. It
  doesn't use `load_definitions`, because a broker that imports definitions at boot doesn't
  create `RABBITMQ_DEFAULT_USER`, and then nothing can log in. This was checked on RabbitMQ 3.12
  and 4.1.

## Queues

Exchange `gridlock`: topic, durable.

| Queue | Bound with | Consumed by |
| --- | --- | --- |
| `triage.report_received` | `report.received` | triage-engine |
| `verifier.report_triaged` | `report.triaged` | verifier |
| `gridlock.dlq` | everything `gridlock.dlx` receives (fanout) | a person: every message here is a report responders can't see |

`report.needs_review` and `incident.updated` have no queue in the MVP, because the console reads
the database (domain-model §6). A publisher using `mandatory` gets them back as unroutable.

**Dead-lettering after 3 attempts (T012 measurement).** Both consumer queues are quorum queues
with `x-delivery-limit: 2`. On RabbitMQ **4.1.4**, a message nacked with requeue arrives in
`gridlock.dlq` on exactly the **3rd** failed delivery. The same holds for a consumer that
crashes holding the message. A limit of `3` gives 4 deliveries; the tests pin both results.
Dead-lettering is `at-least-once`, so a message leaves a queue only once the DLQ has it.

`gridlock.dlq` has `x-delivery-limit: -1` (unlimited). Without it, RabbitMQ 4.x's default limit
of 20 applies, and because the DLQ has no dead-letter exchange of its own, a message inspected
about 20 times would simply be deleted. The management UI's "Get messages" with requeue counts
as an inspection.

## Tests

```bash
cd infra
uv sync
uv run pytest
```

By default the suite builds `postgres/Dockerfile`, starts it and `rabbitmq:4.1-management` in
Docker, so Docker must be running. To use servers you already run, set
`GRIDLOCK_TEST_DATABASE_URL` (a superuser DSN), `GRIDLOCK_TEST_AMQP_URL` and
`GRIDLOCK_TEST_RABBITMQ_API` (see `tests/conftest.py`). With neither, the suite errors rather
than skipping.
