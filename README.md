# GridLock

**A triage engine for community safety reports.**
_Theme: AI for Safer Communities._

Community safety reporting in South Africa runs on group chats. A home invasion arrives in the same
undifferentiated feed as a broken streetlight, with the same visual weight, and response time pays
for it. GridLock is the layer that ranks: it ingests reports, assigns each one a priority tier
grounded in local context, corroborates it against other reports from the same area, and hands
responders a queue ordered by what actually matters.

> **Status: pre-implementation.** The architecture and contracts are designed; no service code
> exists yet. The tree below is what is being built, not what is here.

---

## How it works

```
Reporter ──POST──▶ ingest-api ──persist──▶ PostGIS
(Expo app)          │  202 in ≤200ms
                    └──publish──▶ RabbitMQ ──▶ triage-engine ──▶ rag-index
                                       │         (LangChain)      (landmarks)
                                       │              │
                                       │           tier + reason
                                       ▼              ▼
                                   verifier ◀── report.triaged
                              (geo-grid corroboration)
                                       │
Responder ◀──ranked queue──── ingest-api ◀── PostGIS
(web console)
```

Four ideas do the work:

**Acknowledge first, think later.** A person reporting a break-in on a bad connection gets a `202`
in under 200ms. The AI never sits in that path. Triage happens asynchronously off a durable queue,
so a model outage degrades the ranking — it does not drop the report.

**Four tiers, and a reason for each.** `MONITOR` → `ADVISORY` → `URGENT` → `CRITICAL_DISPATCH`.
Every assignment is persisted with the model's stated reason, the evidence it retrieved, the model
id and the prompt version. A tier a responder can't interrogate is a tier they can't act on.

**Grounded in real places.** People describe locations as "the Spar on Vilakazi", not as
coordinates. A retrieval step resolves landmark language against a local index. When it can't — no
match, or two matches — the location is recorded as *unresolved*, never guessed. A confidently wrong
address is worse than an honest blank.

**Corroboration is counted, not believed.** Reports landing in the same geographic grid cell inside
the same time window are grouped into one incident, and responders see the count. Six independent
reports and one panicked one look different on the screen, because they are different.

## Architecture

| Layer | Technology | Function |
| --- | --- | --- |
| Ingest | FastAPI (Python 3.13) | Asynchronous intake; persist-then-publish, acknowledge immediately |
| Messaging | RabbitMQ 4.x | Topic exchange, durable queues, dead-letter queue — services stay decoupled |
| Orchestration | LangChain | Portable triage logic: prompts as versioned files, no transport types in the chain |
| Retrieval | Vector index over local landmarks | Grounds tier assignment in context that actually exists |
| Verification | Geo-grid + time window | Cross-references reports into incidents; corroboration is a `COUNT(*)` |
| Data | PostgreSQL 17 + PostGIS | Reports, results, incidents — with the grounding rules enforced as DB constraints |
| Reporter client | Expo SDK 57 + React Native 0.87 | The resident-facing app: submit on bad signal, queue offline, never guess a location |
| Responder client | React 19 + Vite + shadcn/ui | The ranked queue console responders work from |

The triage chain deliberately imports no web-framework or database types. The brief commits to a
possible Java migration; that promise is only real if the logic is portable, so it is tested rather
than asserted.

Because the services are decoupled by a broker, adding a new report source later — CCTV, IoT
sensors — is a new publisher on an existing exchange, not a rebuild. Reports carry a
`source_channel` field from day one for that reason.

## Repository layout

```
apps/web/              React + Vite + shadcn/ui — responder console
apps/mobile/           Expo + React Native — resident reporter app
services/
  ingest-api/          FastAPI: accept → persist → publish → 202
  triage-engine/       LangChain: consume → retrieve → tier → validate → publish
  verifier/            geo-grid + time-window corroboration; owns Incident
  rag-index/           landmark ingestion, embedding, retrieval
packages/contracts/    shared enums and payload models — imported, never re-declared;
                       TypeScript types are generated from them, never hand-written
infra/                 RabbitMQ definitions, PostGIS init and migrations
data/landmarks/        committed source data the retrieval index is rebuilt from
docker-compose.yml     the whole system, locally
```

## Running it

The backing services run now; the four services join `docker-compose.yml` as they are built.

```bash
uv python install 3.13     # the project pins 3.13
cp .env.example .env       # set your own local passwords
docker compose up -d       # PostgreSQL (PostGIS + pgvector), RabbitMQ, and the queue topology
```

See [`infra/README.md`](infra/README.md) for ports, queues and dead-lettering.

Requirements: Python 3.13, Node 24 LTS, Docker with Compose v2+.

## Design principles

These are enforced in review and in tests, not just written down:

1. **Never invent an incident, a location, a corroborating report, or a tier.** Every field in a
   result traces to the reporter's own text or to a landmark in the index. No match means `null` and
   a tier assigned from the text alone — never a plausible-sounding suburb. A model returning a tier
   outside the four legal values is rejected for human review, never rounded to the nearest one.
2. **Never lose a report.** Persist before publish; acknowledge before triage. Broker down, model
   down, index down — the report still exists and still reaches a responder, with the failure
   attached. Losing a safety report is the one unacceptable outcome; being unable to rank one is
   merely bad.
3. **Every dispatch-affecting decision is explainable.** Tier, reason, evidence, model id and prompt
   version are persisted together, always.
4. **Corroboration is arithmetic.** It counts persisted reports. It never asks a model whether two
   reports describe the same event.

## Team

| | Backend | Client surface |
| --- | --- | --- |
| Katlego ([@Katlego-tech](https://github.com/Katlego-tech)) | `triage`, `rag` — the LangChain path | `apps/mobile` — the reporter app |
| Kamo | `ingest`, `verify`, `infra` — the deterministic path | `apps/web` — the responder console |

Both of us work the backend first, split at the LLM seam so the tightly-coupled pieces stay with one
person: `triage` calls `rag` on every report, and `ingest` and `verify` share the grid-cell and
persistence model. The client surfaces split only once the backend is up.

Because two people build two clients against one API, `packages/contracts` is the seam between us —
its Pydantic models are the single source of truth and the TypeScript types are generated from them.
A schema disagreement should break a build, not a demo.

## Contributing

Branch → PR into `main` → green CI → review → merge. No direct pushes to `main`. Commits are
formatted `type(scope): short description`.

### Setting up your clone

Do this once, immediately after cloning, before you write anything.

**1. Install the hooks.**

```bash
bash install-hooks.sh
```

**2. Read what it prints.** It does not just install — it proves the hook works, because a gate
nobody has watched fire is indistinguishable from no gate:

```
Installing the pre-push gate for this clone...
  core.hooksPath = .githooks

Self-test 1/2: does the hook reject a push to main?
  ok -- pushes to main are rejected.

Self-test 2/2: what will the gate actually run here?
  ...
```

If self-test 1 says `FAIL -- the hook allowed a push to main`, stop. Don't push anything until it
passes; at that point you have no protection at all and neither does the branch.

**3. Verify it stuck.**

```bash
git config --get core.hooksPath
```

This must print `.githooks`. Anything else — blank, an error — means the hook is not installed, no
matter what step 1 appeared to say.

**Why this is a script and not a sentence.** `core.hooksPath` lives in `.git/config`, and
`.git/config` is **never cloned**. Hooks are not version-controlled state. So the person who set the
repo up has the gate and nobody else does, silently, until they run this. There is no server-side
branch protection on this repository — GitHub gates that behind a paid plan for private repos — so
this hook is not a convenience, it is the only thing standing between a stray `git push` and `main`.
CI cannot cover for it: CI runs *after* the ref has already moved, and reports. It cannot refuse.

If it is working, a push to `main` looks like this — this is the gate doing its job, not a bug:

```
Direct pushes to 'main' are not allowed.
Push a feature branch and open a PR:  git push -u origin feat/<name>
```

`--no-verify` skips all of it. If the gate is wrong, fix `scripts/gate.sh`; don't reach for
`--no-verify` twice.

### Before every PR

```bash
bash scripts/gate.sh
```

It must be green — and green because it *ran*. Read the check count it prints. A gate that finds
nothing to run in a tree that has code is a misconfiguration, not a pass, and the script will say so
rather than quietly exiting zero.

What it runs, once there is code: per Python project `ruff check`, `ruff format --check`, `pyright`
and `pytest`; per Node package its `lint`, `test` and `build` scripts; then across the whole repo
the placeholder and secret sweeps, a dependency vulnerability scan of every committed lockfile, and
a duplication check. A check that cannot run fails, so your machine needs:

- `ruff` and `pyright` as dev dependencies of each Python project — `uv add --dev ruff pyright`
- a `lint` script in each `package.json` — e.g. `"eslint . && prettier --check . && tsc --noEmit"`
- a committed lockfile for every project (`uv lock`, or the package manager's own)
- [`osv-scanner`](https://google.github.io/osv-scanner/installation/) on your `PATH`, and `npx`
  (it comes with Node) for the duplication check
- Docker running, for `infra/`'s tests, which run against real Postgres and RabbitMQ

A vulnerability with no fixed release yet is declared in `osv-scanner.toml` at the repo root, with
its reason naming the follow-up that will close it (`T012: …` or `#12: …`). A deliberate copy of
code is marked with `jscpd:ignore-start` / `jscpd:ignore-end` and a reason beside it.

Every check lives in `scripts/gate.sh`, and both the pre-push hook and CI run that same file. If you
need a new check, add it there — never to the workflow alone, or the two start disagreeing about the
same commit and people learn to ignore the local one.
