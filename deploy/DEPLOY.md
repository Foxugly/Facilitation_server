# Delegation Poker — Deployment (fleet onboarding, OPERATIONS.md §3.12)

Backend `facilitation-api.foxugly.com`, **ASGI/daphne on `127.0.0.1:8007`** (the fleet's only
ASGI + WebSocket site). Frontend `facilitation.foxugly.com` lives in `Poker_frontend`.

CI/CD is **OIDC → SSM** on push to `main` (`.github/workflows/deploy.yml`): tests run, then
root installs units / nginx vhost / the env-fetch oneshot **from the committed git blob**
(§3.10/§3.11) and runs `deploy.sh` as `django`. **Nothing here is applied automatically until
the off-box prerequisites below exist.** IAM admin is done **off-box** (the box's default aws
identity is `certbot-route53`, §3.5).

## ⚠️ The one fleet exception: ASGI + WebSocket

Every other site is gunicorn/WSGI. Poker runs **daphne** and needs the nginx `location /ws/`
upgrade block (`deploy/nginx/facilitation-api.conf`). Redis is required for the Channels layer in prod
(multi-process) and is already on the box (used by pushit) — Poker uses DB index 3 (`REDIS_URL`).

## Off-box prerequisites (do once, in order)

1. **PostgreSQL** (on-box, one-off): `CREATE ROLE poker LOGIN PASSWORD '…'; CREATE DATABASE poker
   OWNER poker; ALTER SCHEMA public OWNER TO poker;`
2. **SSM secrets** (off-box, admin): edit + run `deploy/seed-parameter-store.sh` (fills
   `/poker/prod/*`). Then grant the instance role **`foxugly-fleet-ec2`**
   `ssm:GetParametersByPath`/`GetParameters` on **both** `…:parameter/poker/prod` **and**
   `…/poker/prod/*` (+ `kms:Decrypt` on `aws/ssm`).
3. **OIDC deploy role** (off-box, admin): create **`poker-deploy`**, trust pinned to
   `StringEquals … :sub = repo:Foxugly/Poker_server:environment:production` (no wildcard);
   least-priv (`ssm:SendCommand` on the instance + `AWS-RunShellScript`, `ssm:GetCommandInvocation`).
   GitHub repo secrets: `AWS_DEPLOY_ROLE_ARN`, `EC2_INSTANCE_ID`.
4. **sudoers** (root, out-of-band): `/etc/sudoers.d/poker-deploy` `0440 root:root`, `visudo -c`,
   grant `django (root) NOPASSWD` ONLY `/bin/systemctl restart poker-*` + `/usr/sbin/nginx -t` +
   `/bin/systemctl reload nginx`, with `!setenv,!env_keep`.
5. **DNS**: `facilitation-api.foxugly.com` (+ `facilitation.foxugly.com` for the SPA) A/ALIAS → box IP. TLS is
   already covered by the shared wildcard `*.foxugly.com` — **never** run per-subdomain certbot (§3.6).
6. **Sentry**: create projects `poker-backend` + `poker-frontend` (org `foxugly-srl`, de.sentry.io);
   put the backend DSN in `/poker/prod/SENTRY_DSN`.
7. **Monitoring**: one UptimeRobot HTTP monitor on `https://facilitation-api.foxugly.com/health/`
   (keyword `"status": "ok"`), added in the dashboard.

## First deploy

Push to `main` → the workflow installs units + nginx from the git blob, `daemon-reload`, enables
`facilitation-env-fetch`/`facilitation-asgi`/`facilitation-celery`/`facilitation-celery-beat`, runs `deploy.sh` (migrate,
collectstatic, seed the standard deck, restart), and `nginx -t && reload`.

## Verify

- `curl https://facilitation-api.foxugly.com/health/` → `{"status": "ok", ...}` 200.
- A browser WS to `wss://facilitation-api.foxugly.com/ws/rooms/<code>/` upgrades (101) — create a room in
  the SPA and confirm live participation.
- `sudo find <tree> ! -type l \( -perm /020 -o -perm /004 \)` reports 0; `sudo -l -U django` shows
  only the `poker-*` restart + nginx grant.

## Content dependency

Card artwork (7 illustrations + card back) is uploaded via Django admin (`/admin/`), not code.
Until then cards render with the number + translated name overlay only (functional, no image).
