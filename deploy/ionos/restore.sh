#!/usr/bin/env bash
# Restore an Eurskem AI backup created by backup.sh.
#
# Usage:
#   restore.sh /opt/eurskem/backups/eurskem-<stamp>.tar.gz
#
# The archive layout is the exact inverse of backup.sh:
#   mongo.archive.gz      mongodump --archive --gzip of the app database
#   minio/                mirrored object-store bucket
#   workflows.tar.gz      shared workflows directory
#   env.production        the environment file captured at backup time
#   weaviate-data.tar.gz  weaviate volume contents
#   redis-data.tar.gz     redis volume contents
#
# DANGER: this overwrites the live database, objects, and volumes. Run it
# only when you mean to roll the system back to the backup point.
set -euo pipefail

archive=${1:?usage: restore.sh /path/to/eurskem-<stamp>.tar.gz}
root_dir=/opt/eurskem
release_dir=${root_dir}/current
env_file=${root_dir}/shared/.env.production

if [[ ! -f "${archive}" ]]; then
  echo "Archive not found: ${archive}" >&2
  exit 1
fi
if [[ ! -d "${release_dir}" || ! -f "${env_file}" ]]; then
  echo "Production release or environment file is missing." >&2
  exit 1
fi
if [[ -f "${archive}.sha256" ]]; then
  ( cd "$(dirname "${archive}")" && sha256sum -c "$(basename "${archive}").sha256" )
else
  echo "WARNING: no .sha256 sidecar; integrity of the archive is unverified." >&2
fi

echo "This will OVERWRITE the live database, objects, and volumes with:"
echo "  ${archive}"
read -r -p "Type RESTORE to continue: " confirmation
if [[ "${confirmation}" != "RESTORE" ]]; then
  echo "Aborted." >&2
  exit 1
fi

work_dir=$(mktemp -d "${root_dir}/backups/.restore.XXXXXX")
cleanup() { rm -rf -- "${work_dir}"; }
trap cleanup EXIT

tar -C "${work_dir}" -xzf "${archive}"
cd "${release_dir}"

compose() {
  docker compose --env-file "${env_file}" \
    -f docker-compose.production.yml "$@"
}

# ---- stop writers before touching state ------------------------------------
compose stop caddy app

# ---- Mongo ------------------------------------------------------------------
echo "Restoring Mongo database..."
compose exec -T mongo sh -c \
  'exec mongorestore --quiet --drop --archive --gzip' \
  < "${work_dir}/mongo.archive.gz"

# ---- MinIO objects -----------------------------------------------------------
echo "Restoring MinIO objects..."
compose run --rm -T \
  -v "${work_dir}/minio:/backup:ro" \
  --entrypoint /bin/sh minio-init -c \
  'mc alias set target http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc mirror --overwrite /backup/"$MINIO_BUCKET" target/"$MINIO_BUCKET"'

# ---- Weaviate + Redis volumes ------------------------------------------------
echo "Restoring Weaviate and Redis volumes..."
compose stop weaviate redis
docker run --rm \
  -v eurskem-ai_weaviate-data:/target \
  -v "${work_dir}:/backup:ro" \
  alpine:3.22 \
  sh -c 'rm -rf /target/* /target/.[!.]* 2>/dev/null || true; tar -C /target -xzf /backup/weaviate-data.tar.gz'
docker run --rm \
  -v eurskem-ai_redis-data:/target \
  -v "${work_dir}:/backup:ro" \
  alpine:3.22 \
  sh -c 'rm -rf /target/* /target/.[!.]* 2>/dev/null || true; tar -C /target -xzf /backup/redis-data.tar.gz'

# ---- shared workflows ---------------------------------------------------------
echo "Restoring shared workflows..."
tar -C "${root_dir}/shared" -xzf "${work_dir}/workflows.tar.gz"

# ---- restart ------------------------------------------------------------------
echo "Starting the stack..."
compose up -d

# ---- readiness ----------------------------------------------------------------
ready=0
for _ in $(seq 1 60); do
  if compose exec -T app \
    python -c \
      "import urllib.request; from app.config import settings; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/ready', headers={'Host': settings.allowed_hosts[0]}), timeout=5)" \
    >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 5
done
if [[ ${ready} -ne 1 ]]; then
  echo "Restore applied, but /ready did not come up — inspect: compose logs" >&2
  exit 1
fi

# env.production from the archive is informational only: never silently swap
# the live environment file. It remains inside the archive for reference;
# extract it manually if you need to compare or reinstall it:
#   tar -xzf <archive> env.production
echo "Restored from ${archive}."
