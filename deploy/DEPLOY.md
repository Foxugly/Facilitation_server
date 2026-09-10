# Facilitation — Deployment (fleet onboarding, OPERATIONS.md §3.12)

Backend `facilitation-api.foxugly.com`, **ASGI/daphne on `127.0.0.1:8009`** (the fleet's only
ASGI + WebSocket site). Frontend `facilitation.foxugly.com` lives in `Facilitation_frontend`.

CI/CD is **OIDC → SSM** on push to `main` (`.github/workflows/deploy.yml`): tests run, then
root installs units / nginx vhost / the env-fetch oneshot **from the committed git blob**
(§3.10/§3.11) and runs `deploy.sh` as `django`. **Nothing here is applied automatically until
the off-box prerequisites below exist.** IAM admin is done **off-box** (the box's default aws
identity is `certbot-route53`, §3.5).

## Ports, Redis, database — valeurs relevées sur la box le 2026-09-10

Les ports `8000`–`8008` sont **tous occupés** (`8006` = daphne Poker, `8007` = gunicorn
billing, `8008` = daphne Fabric). Facilitation prend donc **8009**, premier libre de la série
— le suivant occupé est `8125` (netdata).

Redis : les index `db0`–`db4` sont utilisés ; Facilitation prend **db5** (`REDIS_URL`). Un
index déjà pris mélangerait les channel layers de deux applications.

PostgreSQL : aucune base `facilitation` n'existe (les bases en place sont quizonline, pushit,
ical, trainingmanager, tm, foxugly, poker, billing, fabric).

## ⚠️ The one fleet exception: ASGI + WebSocket

Every other site is gunicorn/WSGI. Facilitation runs **daphne** and needs the nginx
`location /ws/` upgrade block (`deploy/nginx/facilitation-api.conf`). Redis is required for the
Channels layer in prod (multi-process) and is already on the box.

## Off-box prerequisites (do once, in order)

1. **PostgreSQL** (on-box, one-off): `CREATE ROLE facilitation LOGIN PASSWORD '…';
   CREATE DATABASE facilitation OWNER facilitation; ALTER SCHEMA public OWNER TO facilitation;`
2. **SSM secrets** (off-box, admin): edit + run `deploy/seed-parameter-store.sh` (fills
   `/facilitation/prod/*`). Then grant the instance role **`foxugly-fleet-ec2`**
   `ssm:GetParametersByPath`/`GetParameters` on **both** `…:parameter/facilitation/prod` **and**
   `…/facilitation/prod/*` (+ `kms:Decrypt` on `aws/ssm`).
3. **OIDC deploy role** (off-box, admin): create **`facilitation-deploy`**, trust pinned to
   `StringEquals … :sub = repo:Foxugly/Facilitation_server:environment:production` (no wildcard);
   least-priv (`ssm:SendCommand` on the instance + `AWS-RunShellScript`, `ssm:GetCommandInvocation`).
   GitHub repo secrets: `AWS_DEPLOY_ROLE_ARN`, `EC2_INSTANCE_ID`.
4. **sudoers** (root, out-of-band): `/etc/sudoers.d/facilitation-deploy` `0440 root:root`,
   `visudo -c`, grant `django (root) NOPASSWD` ONLY `/bin/systemctl restart facilitation-*` +
   `/usr/sbin/nginx -t` + `/bin/systemctl reload nginx`, with `!setenv,!env_keep`.
5. **DNS**: `facilitation-api.foxugly.com` (+ `facilitation.foxugly.com` for the SPA) A/ALIAS → box IP.
   TLS is already covered by the shared wildcard `*.foxugly.com` — **never** run per-subdomain
   certbot (§3.6).
6. **Sentry**: create projects `facilitation-backend` + `facilitation-frontend` (org `foxugly-srl`,
   de.sentry.io); put the backend DSN in `/facilitation/prod/SENTRY_DSN`.
7. **Billing**: register the slug `facilitation` with `billing-api.foxugly.com`, and set
   `BILLING_BASE_URL` + `BILLING_APP_SECRET` in SSM. Until both are set, billing stays inert:
   teams remain open and checkout answers 503 (deliberate — the site deploys without it).
8. **Monitoring**: one UptimeRobot HTTP monitor on `https://facilitation-api.foxugly.com/health/`
   (keyword `"status": "ok"`), added in the dashboard.

## First deploy

Créer d'abord le répertoire `/var/www/django_websites/Facilitation_server` (clone du repo,
`django:www-data`, `750`) et son `.venv` — le déploiement met à jour un dépôt existant, il ne
le crée pas.

Push to `main` → the workflow installs units + nginx from the git blob, `daemon-reload`, enables
`facilitation-env-fetch`/`facilitation-asgi`/`facilitation-celery`/`facilitation-celery-beat`,
runs `deploy.sh` (migrate, collectstatic, seed the standard deck, restart), and
`nginx -t && reload`.

## Verify

- `curl https://facilitation-api.foxugly.com/health/` → `{"status": "ok", ...}` 200.
- A browser WS to `wss://facilitation-api.foxugly.com/ws/rooms/<code>/` upgrades (101) — create a
  room in the SPA and confirm live participation.
- `sudo ss -lntp | grep 8009` → daphne, and nothing else on that port.
- `sudo find <tree> ! -type l \( -perm /020 -o -perm /004 \)` reports 0; `sudo -l -U django` shows
  only the `facilitation-*` restart + nginx grant.

## Content dependency

Card artwork is uploaded via Django admin (`/admin/`), not code. Until then cards render with
the number + translated name overlay only (functional, no image).
