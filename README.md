# QueryPilot

QueryPilot is a schema-constrained natural-language analytics agent for ArangoDB. It uses
LangChain to produce a typed query plan, LangGraph for the controlled workflow, and
Docker-hosted Milvus for semantic catalog retrieval. The model never writes AQL. A server-side
compiler validates the plan against the discovered `SchemaSnapshot` and deterministically
produces read-only AQL before policy checks and ArangoDB `EXPLAIN` validation.

The current milestone implements a multi-source query path:

```text
question -> Milvus catalog retrieval -> typed query plan -> deterministic AQL compiler
         -> policy + EXPLAIN -> read-only execution -> result summary
```

Each registered ArangoDB source gets its own connection, discovered schema, Milvus
catalog release, and read-only query policy. A query is routed by `source_id`; it never
falls through to the bundled commerce database.

See [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md) for the complete architecture and
milestones.

The bundled React workbench displays the live LangGraph trajectory, Milvus evidence,
compiled AQL, policy status, result table, and Chinese answer on the same screen.

## Local prerequisites

- Python 3.11+
- Docker Engine with Docker Compose v2
- An OpenAI-compatible chat and embedding endpoint

## Development

```bash
python -m venv .venv
python -m pip install -e ".[dev,postgres]"
pytest
```

Copy `.env.example` to `.env` and replace every placeholder before starting the stack.
Never commit `.env`.

On Windows PowerShell, use `.venv\Scripts\python.exe` instead of `python` after creating
the environment.

## Start the data services

```bash
docker compose --env-file .env -f infra/compose.yaml up -d \
  postgres arangodb etcd minio milvus-standalone
```

Initialize the deterministic commerce dataset and read-only ArangoDB account:

```bash
docker compose --env-file .env -f infra/compose.yaml \
  --profile tools run --rm seed
```

The dataset uses a pinned seed and contains 120 users, 160 products, 1,200 orders,
and 4,360 graph edges.

Publish the versioned semantic catalog to Milvus. This step calls the configured
Embedding API:

```bash
docker compose --env-file .env -f infra/compose.yaml \
  --profile tools run --rm catalog-publish
```

Start the API:

```bash
docker compose --env-file .env -f infra/compose.yaml up -d api
```

The development API and workbench bind only to `127.0.0.1:8000`. Open:

```text
http://127.0.0.1:8000/
```

Verify the API with:

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

Stream a query:

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/runs:stream \
  -H "Content-Type: application/json" \
  -d '{"source_id":"commerce","question":"查询已支付订单"}'
```

## Connect another ArangoDB

Use the **接入数据源** command in the workbench, or call the source endpoint directly:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/sources \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "analytics",
    "url": "http://host.docker.internal:8529",
    "database": "analytics",
    "username": "querypilot_reader",
    "password": "replace-me",
    "sample_size": 50
  }'
```

Onboarding scans accessible non-system collections, samples up to 200 documents per
collection to infer field types, reads Named Graph edge definitions, builds embeddings,
and publishes a source-specific Milvus release. Sample values are not stored in Milvus.

Source credentials are currently held only in API process memory and are never returned
by the HTTP interface. Restarting the API removes dynamically registered sources. Durable
encrypted credential storage is intentionally deferred to the next milestone.

```bash
curl http://127.0.0.1:8000/api/v1/sources
```

## Production deployment

Production uses `infra/compose.prod.yaml` as an override. It removes all host bindings for
PostgreSQL, ArangoDB, Milvus, and FastAPI; only Caddy exposes ports `80/443`. Caddy provides
automatic TLS and Basic Auth. The API applies a per-client sliding-window limit to query and
source-onboarding requests, while `/health/ready` checks PostgreSQL, ArangoDB, and Milvus.
Production onboarding also requires the target hostname in `SOURCE_ALLOWED_HOSTS` to prevent
the public demo from becoming an internal-network scanner.

Use [DEPLOYMENT.md](DEPLOYMENT.md) for server preparation, secret generation, first-time data
initialization, backups, verification, upgrades, and rollback.

```bash
cp .env.production.example .env
bash infra/scripts/deploy.sh
```

## Quality checks

```bash
python -m ruff check apps/api/src apps/api/tests infra/scripts
python -m mypy apps/api/src/querypilot
python -m pytest
cd apps/web && npm ci && npm run build
```

## Evaluation

The repository includes 30 deterministic cases covering execution correctness,
LangGraph trajectory, aggregation, graph traversal, time filters, write rejection,
out-of-scope resources, and prompt injection. The current live baseline is `0.8517`.

```bash
python -m evals.runner --validate-only
python -m evals.runner --cases evals/cases/olist/structured.yaml --validate-only
python -m evals.runner --output evals/reports/current.json
python -m evals.ci_gate --report evals/reports/current.json --baseline evals/baseline.json
```

See [evals/README.md](evals/README.md) for the grounded LLM judge, online replay
configuration, audited BFCL subset, and baseline promotion policy.

The real Milvus integration test is enabled only when `MILVUS_TEST_URI` is set:

```bash
MILVUS_TEST_URI=http://127.0.0.1:19530 python -m pytest \
  apps/api/tests/integration/test_milvus_catalog.py
```

## Security defaults

- Only `22`, `80`, and `443` should be public on the server.
- Development database ports bind to loopback, not all network interfaces.
- QueryPilot uses a dedicated read-only ArangoDB account.
- Dynamic source passwords are kept in memory only and excluded from responses and logs.
- AQL write keywords are rejected before `EXPLAIN`.
- Server-owned limits wrap every candidate query.
- Secrets live in `.env` locally and deployment secrets in CI, never in Git.
