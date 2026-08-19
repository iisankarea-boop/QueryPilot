# QueryPilot production deployment

This runbook targets one Ubuntu 22.04/24.04 server with 4 vCPU, 8 GiB RAM, 60 GiB SSD,
Docker Engine, Docker Compose v2, and a domain name. The LLM and embedding models remain
external APIs.

For a server without a domain, use `infra/compose.private.yaml`. It keeps the API and data
ports on `127.0.0.1`; access the workbench only through an SSH tunnel. Do not expose port
`8000` in the cloud firewall because this mode has no browser authentication or TLS.

## 1. Prepare the server

Point the domain's `A` record at the server before starting Caddy. In the cloud firewall,
allow only TCP `22`, `80`, and `443`, plus UDP `443`. Do not expose PostgreSQL, ArangoDB,
Milvus, MinIO, etcd, or the FastAPI port.

Create 4 GiB swap for peak protection:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Install Docker from Docker's official Ubuntu repository and add the deployment user to the
`docker` group. Clone the repository into `/opt/querypilot` using a deploy key or upload a
release archive. Do not send a server password or production `.env` through chat.

## 2. Configure secrets

```bash
cd /opt/querypilot
cp .env.production.example .env
chmod 600 .env
```

Generate independent values for every `replace-me` entry. Keep the password embedded in
`POSTGRES_DSN` equal to `POSTGRES_PASSWORD` and URL-encode reserved URL characters.
`SOURCE_ALLOWED_HOSTS` is a comma-separated exact hostname allowlist for dynamic ArangoDB
connections. Keep `arangodb` for the bundled container and add only operator-approved hosts.

Generate the Caddy Basic Auth hash without placing the plaintext password in shell history:

```bash
read -rsp 'Demo password: ' DEMO_PASSWORD && echo
DEMO_PASSWORD_HASH="$(docker run --rm caddy:2.10.0-alpine \
  caddy hash-password --plaintext "${DEMO_PASSWORD}")"
unset DEMO_PASSWORD
printf '%s\n' "${DEMO_PASSWORD_HASH}"
```

Place the hash in `.env` inside single quotes so its `$` characters remain literal:

```dotenv
DEMO_PASSWORD_HASH='$2a$14$...'
```

## 3. First deployment

Validate, build, start, and wait for all dependencies:

```bash
bash infra/scripts/deploy.sh
```

Only on a new empty deployment, initialize the bundled commerce database and publish its
Milvus catalog:

```bash
docker compose --env-file .env \
  -f infra/compose.yaml -f infra/compose.prod.yaml \
  --profile tools run --rm seed
docker compose --env-file .env \
  -f infra/compose.yaml -f infra/compose.prod.yaml \
  --profile tools run --rm catalog-publish
```

Restart the API after first-time catalog publication and verify the public endpoint:

```bash
docker compose --env-file .env \
  -f infra/compose.yaml -f infra/compose.prod.yaml restart api
bash infra/scripts/verify-deployment.sh https://querypilot.example.com
```

The workbench is protected by browser Basic Auth. Health endpoints intentionally remain
unauthenticated for monitoring and reveal only dependency names and coarse status.

For a private deployment without a domain, replace `infra/compose.prod.yaml` in the commands
above with `infra/compose.private.yaml`, start the stack, and verify it on the server:

```bash
docker compose --env-file .env \
  -f infra/compose.yaml -f infra/compose.private.yaml up -d --remove-orphans
curl -fsS http://127.0.0.1:8000/health/ready
```

From the operator workstation, keep this SSH session open and browse to
`http://127.0.0.1:8001/`:

```bash
ssh -L 8001:127.0.0.1:8000 ubuntu@server-ip
```

## 4. Backups

Run one backup and inspect both outputs before scheduling it:

```bash
bash infra/scripts/backup.sh
du -sh infra/backups/postgres infra/backups/arango
```

In private mode, set the matching Compose override:

```bash
COMPOSE_OVERRIDE_FILE=infra/compose.private.yaml bash infra/scripts/backup.sh
```

Schedule a daily backup at 03:15 and retain seven days:

```cron
15 3 * * * cd /opt/querypilot && BACKUP_RETENTION_DAYS=7 bash infra/scripts/backup.sh >> /var/log/querypilot-backup.log 2>&1
```

Copy backups to Tencent COS or another server. A backup on the same disk does not protect
against disk or instance loss.

Before restoring, stop `api`, take a fresh backup, and rehearse on a separate server. Restore
PostgreSQL with `pg_restore --clean --if-exists` into the `querypilot` database. Restore the
matching ArangoDB directory with `arangorestore --all-databases true`. Restoration is
intentionally manual because it overwrites live state.

## 5. Upgrade and rollback

Before every upgrade:

```bash
bash infra/scripts/backup.sh
git fetch --tags
git checkout <tested-release-tag>
bash infra/scripts/deploy.sh
```

Rollback by checking out the previous tested tag and running `deploy.sh` again. Database
schema changes require their own documented rollback; the current release creates no product
schema migrations.

Useful checks:

```bash
docker compose --env-file .env \
  -f infra/compose.yaml -f infra/compose.prod.yaml ps
docker compose --env-file .env \
  -f infra/compose.yaml -f infra/compose.prod.yaml logs --tail 100 api caddy
curl -fsS https://querypilot.example.com/health/ready
```

## Known production limitation

Connections added through the workbench are held in API process memory. Restarting `api`
removes those dynamic registrations, although the ArangoDB data and Milvus collections remain.
The bundled `commerce` source is always restored from configuration. Durable encrypted source
credential storage is a separate feature and must be completed before treating this as a
multi-tenant hosted product.
