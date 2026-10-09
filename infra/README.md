# infra — the local stack

PostgreSQL (with PostGIS and pgvector) and RabbitMQ, the schema and queue topology every
GridLock service builds against, and the tests that check them against a real server.

```
postgres/Dockerfile          PostGIS 17 base image + pgvector
postgres/init.sql            enables the postgis and vector extensions (runs once, on a new volume)
postgres/migrations/         the schema, one numbered file per change (0001_init.sql, ...)
postgres/migrate.sh          applies migrations not yet applied; the db-migrate job runs it
rabbitmq/definitions.json    exchanges, queues, bindings, dead-lettering
tests/                       run against real Postgres + RabbitMQ (no mocks)
```

## Run it

From the repo root:

```bash
cp .env.example .env        # set your own local passwords
docker compose up -d
docker compose ps -a        # postgres + rabbitmq "healthy"; db-migrate + rabbitmq-topology "exited (0)"
```

- **Postgres** listens on `localhost:5432`, database `gridlock`, with `postgis` and `vector`
  enabled. The first `up` builds the image (`gridlock/postgres:17-postgis3.6-pgvector0.8`), which
  takes a minute. `init.sql` runs only when the data volume is first created. To start again from
  nothing, run `docker compose down -v && docker compose up -d`.
- **RabbitMQ** listens on `localhost:5672`, with the management UI at <http://localhost:15672>
  (log in with the user and password from your `.env`). Once the broker is healthy, the
  `rabbitmq-topology` job loads `definitions.json` through the management API and exits. It
  doesn't use `load_definitions`, because a broker that imports definitions at boot doesn't
  create `RABBITMQ_DEFAULT_USER`, and then nothing can log in. This was checked on RabbitMQ 3.12
  and 4.1.

## Schema

`postgres/migrations/0001_init.sql` implements domain-model §3: tables `reports`,
`triage_results`, `incidents` and `landmarks`, field for field with the class diagram. The test
suite reads the diagram out of `docs/design/domain-model.md` and fails if the two differ in
either direction. `corroboration_count` is not a column; it is computed when the queue is read.

The database enforces the rules itself, so no service (or hand-typed `psql`) can get round
them:

- **Grounding.** A result with `location_confidence` `AMBIGUOUS` or `UNKNOWN` can't have a
  `grid_cell` or `resolved_coords`. That's four named constraints, e.g.
  `triage_ambiguous_has_no_grid_cell`.
- **Grid cells are H3 res-9 cells.** A `h3_cell` domain accepts 15 lower-case hex characters
  starting `89`, so a suburb name can't end up where incidents are grouped. verification.md
  §6.3 says `VARCHAR(15)`; this is the same length, with the format checked as well.
- **One outcome per result.** A result has either `tier` + `reason` or a `failure_reason`, never
  both and never neither.
- **Report lifecycle.** `state` moves only along the §5 diagram. `TRIAGED` needs a result with a
  tier, and `NEEDS_REVIEW` a result with a failure. Write the result first, then move the report.
- **Evidence and audit trail.** What the reporter sent is never rewritten. Reports and results
  are never deleted or truncated. A resolved report's `incident_id` is final, and a report can
  only join an `OPEN` incident.
- **Incidents stay `OPEN`.** `MERGED` and `CLOSED` are reserved until closing and merging are
  designed (domain-model §10). `opened_at` and `last_report_at` are report times with no
  `now()` default: the verifier passes `received_at` (verification.md invariant 4).
- **Evidence** is stored in the result as JSON `[{landmark_id, text, similarity}, …]` with exactly
  those keys. That way a rebuilt landmark index can never change the evidence behind a past
  decision.

What the database can't do yet: **stop a superuser.** Compose's `POSTGRES_USER` is a superuser
and can disable triggers (`SET session_replication_role = replica`) or drop constraints. Each
service should connect as its own non-superuser role, created alongside the first service that
uses the database. Until then, the guarantees above hold against application writes, not
against a superuser who sets out to bypass them.

It also can't **reject off-globe coordinates.** PostGIS silently moves latitude
−95 to −85 while parsing the value, before any constraint sees it, and stores a real-looking
point about 1,100 km away. Whatever accepts coordinates (the contracts model, ingest-api) must
return 422 for lat outside [−90, 90] or lon outside [−180, 180]. A test pins this behaviour.

### Changing the schema

Add a new file, `postgres/migrations/0002_<what>.sql`. Never edit a merged one. `migrate.sh`
runs each file once, in filename order, in one transaction with its record in
`schema_migrations`, so a failing migration leaves no trace. It runs on every
`docker compose up` and skips what's already applied. Every service that uses the database
waits for it:

```yaml
depends_on:
  db-migrate: { condition: service_completed_successfully }
```

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
