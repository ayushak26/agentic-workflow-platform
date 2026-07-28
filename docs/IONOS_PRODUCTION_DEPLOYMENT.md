# IONOS production deployment

The supplied production topology is designed for one Ubuntu 24.04 IONOS VPS.
Only Caddy publishes ports 80/443. MongoDB, Weaviate, MinIO and Redis stay on
an internal Docker network; Grafana and Prometheus bind only to VPS loopback.

## 1. Prepare DNS and the IONOS firewall

1. Assign a static public IP to the VPS.
2. Create an `A` record such as `ai.example.com` pointing to that IP.
3. In the IONOS firewall policy, allow inbound SSH from your administration
   IP, plus `80/tcp`, `443/tcp`, and `443/udp`. Deny the database and dashboard
   ports.
4. Wait for the DNS record to resolve before the first deployment so Caddy can
   obtain the TLS certificate.

IONOS documents the same DNS, firewall, and Let's Encrypt prerequisites in its
[Ubuntu SSL deployment guide](https://www.ionos.com/help/server-cloud-infrastructure/akkordeons-zu-vps/server-administration-vps/configuring-ubuntu-2404-n8n-for-use-with-ssl-ssl-certificate-from-lets-encrypt/).

## 2. Bootstrap the VPS once

Copy the repository to a temporary directory, then run:

```bash
sudo bash deploy/ionos/setup_host.sh
```

The script installs Docker from Docker's Ubuntu repository, enables UFW and
Fail2ban, creates the `eurskem-deploy` user, and prepares `/opt/eurskem`.
Log out and back in before using Docker as the deployment user.

## 3. Generate the server environment

On a trusted machine, from the repository root:

```bash
python scripts/generate_production_env.py \
  --domain ai.example.com \
  --email admin@example.com \
  --output .env.production
```

Edit the generated file and add `OPENAI_API_KEY`. Add
`ANTHROPIC_API_KEY` when Anthropic should be available as a primary/fallback.
Then validate and transfer it:

```bash
python scripts/production_preflight.py --env-file .env.production
scp .env.production eurskem-deploy@SERVER_IP:/tmp/.env.production
ssh eurskem-deploy@SERVER_IP \
  'install -m 0600 /tmp/.env.production /opt/eurskem/shared/.env.production && rm /tmp/.env.production'
```

Never commit this file and never store these production provider keys in the
ordinary CI or deployment workflows.

## 4. Configure GitHub

Create a protected GitHub environment named `production`. Add:

| Type | Name | Value |
|---|---|---|
| Environment secret | `DEPLOY_SSH_KEY` | Private Ed25519 key for `eurskem-deploy` |
| Environment secret | `DEPLOY_KNOWN_HOSTS` | Output of `ssh-keyscan -H SERVER_IP` verified against the VPS host key |
| Environment variable | `DEPLOY_HOST` | VPS IP or deployment hostname |
| Environment variable | `DEPLOY_PORT` | SSH port, normally `22` |
| Environment variable | `DEPLOY_USER` | `eurskem-deploy` |
| Environment variable | `PRODUCTION_URL` | `https://ai.example.com` |

Add the public half of `DEPLOY_SSH_KEY` to
`/home/eurskem-deploy/.ssh/authorized_keys`. Require approval for the
`production` environment if your GitHub plan permits it. Protect `main`,
require CI, Code Owner review, secret scanning, and push protection.

After a push to `main`, `.github/workflows/deploy-ionos.yml` runs only when
`CI` succeeds. It checksums an immutable Git archive, verifies the VPS host key,
builds the release on the server, waits for `/ready`, runs the 100-user load
gate, switches the `current` symlink, and performs a public HTTPS smoke test.
Failed readiness rebuilds and restores the previous release.

## 5. Create the first application administrator

After the first successful deployment:

```bash
cd /opt/eurskem/current
docker compose --env-file /opt/eurskem/shared/.env.production \
  -f docker-compose.production.yml \
  exec app python scripts/manage_user.py upsert \
  --username ayush --role admin
```

Enter the password only at the interactive prompt. Passwords are stored as
Argon2 hashes. The development bypass is rejected in production.

## 6. Verify and operate

```bash
python scripts/smoke_production.py --base-url https://ai.example.com

docker compose --env-file /opt/eurskem/shared/.env.production \
  -f /opt/eurskem/current/docker-compose.production.yml ps
```

View private dashboards through an SSH tunnel:

```bash
ssh -L 3001:127.0.0.1:3001 -L 9090:127.0.0.1:9090 \
  eurskem-deploy@SERVER_IP
```

Grafana is then at `http://127.0.0.1:3001` and Prometheus at
`http://127.0.0.1:9090`. Neither is internet-facing.

Create a local recovery archive:

```bash
bash /opt/eurskem/current/deploy/ionos/backup.sh
```

Copy every resulting archive and checksum to encrypted, off-server storage and
regularly test restoration on another VPS. The backup contains production data
and `.env.production`, so treat it as a secret. Schedule it during a quiet
period because Weaviate and Redis are briefly stopped for a consistent copy.

## 7. Important production rules

- Do not run `docker compose down -v`; it deletes persistent data volumes.
- Do not rotate MongoDB/MinIO bootstrap credentials only by editing the
  environment after volumes exist. Create/rotate the live service accounts
  first, then change the app configuration.
- Keep provider projects separate, set low budget alerts, and rotate keys.
- Review `/ready`, provider error rates, p95 latency, cache hit rate, and daily
  user/global cost before increasing traffic.
- The code is deployable without `paper-search-mcp`. If enabled, install it at
  the configured server path and include `mcp:paper-search-mcp` in readiness.
