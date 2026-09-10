#!/usr/bin/env bash
# =============================================================================
# Delegation Poker — Fetch environment from AWS SSM Parameter Store into tmpfs.
#
# Run as root by facilitation-env-fetch.service (oneshot) at boot, BEFORE the ASGI /
# celery units start. The file lives in /run (tmpfs): never on disk, re-fetched
# each boot. Source of truth = SSM /poker/prod/* (eu-west-1), read via the EC2
# instance role over IMDS (no AWS keys on disk).
#
# §3.10: this script runs as root, so it is installed root:root 0755 at
# /usr/local/sbin/facilitation-env-fetch.sh (NOT executed from the django tree). This
# file is the versioned source, copied there by root from the committed git blob.
# =============================================================================
set -euo pipefail
umask 077   # temp files (which briefly hold decrypted secrets) are root-only.

SSM_PREFIX="/poker/prod"
AWS_REGION="eu-west-1"
RUN_DIR="/run/poker"
ENV_FILE="$RUN_DIR/.env"
TMP_FILE="$RUN_DIR/.env.tmp"
RAW_FILE="$RUN_DIR/.ssm.json"
OWNER="django:www-data"

mkdir -p "$RUN_DIR"
# 750 root:www-data — root writes it; django (www-data group) can traverse; the
# .env itself stays 640 so its contents remain protected (§3.5).
chmod 750 "$RUN_DIR"
chown root:www-data "$RUN_DIR"

aws ssm get-parameters-by-path \
    --path "$SSM_PREFIX" \
    --recursive \
    --with-decryption \
    --region "$AWS_REGION" \
    --output json > "$RAW_FILE"

python3 - "$SSM_PREFIX" "$TMP_FILE" "$RAW_FILE" <<'PY'
import json, sys

prefix, tmp_path, raw_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(raw_path) as fh:
    params = json.load(fh).get("Parameters", [])

if not params:
    sys.stderr.write(f"ERROR: no parameters under {prefix}; refusing to write an empty env.\n")
    sys.exit(1)

lines = []
for p in params:
    key = p["Name"][len(prefix):].lstrip("/")
    value = p["Value"].strip("\r\n")
    if "\n" in value or "\r" in value:
        sys.stderr.write(f"ERROR: value for {key} contains an internal newline; refusing.\n")
        sys.exit(1)
    lines.append(f"{key}={value}")

with open(tmp_path, "w") as fh:
    fh.write("\n".join(sorted(lines)) + "\n")
PY

rm -f "$RAW_FILE"

if [ ! -s "$TMP_FILE" ]; then
    echo "ERROR: assembled env file is empty; keeping previous $ENV_FILE." >&2
    rm -f "$TMP_FILE"
    exit 1
fi

chmod 640 "$TMP_FILE"
chown "$OWNER" "$TMP_FILE"
mv -f "$TMP_FILE" "$ENV_FILE"

echo "Wrote $(wc -l < "$ENV_FILE") variables to $ENV_FILE."
