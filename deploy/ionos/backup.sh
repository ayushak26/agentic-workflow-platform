#!/usr/bin/env bash
set -euo pipefail

root_dir=/opt/eurskem
release_dir=${root_dir}/current
env_file=${root_dir}/shared/.env.production
backup_root=${root_dir}/backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
work_dir=$(mktemp -d "${backup_root}/.backup-${stamp}.XXXXXX")
archive=${backup_root}/eurskem-${stamp}.tar.gz
stopped=0

cleanup() {
  if [[ ${stopped} -eq 1 && -d ${release_dir} ]]; then
    cd "${release_dir}"
    docker compose --env-file "${env_file}" \
      -f docker-compose.production.yml up -d
  fi
  if [[ -d ${work_dir} ]]; then
    rm -rf -- "${work_dir}"
  fi
}
trap cleanup EXIT

if [[ ! -d ${release_dir} || ! -f ${env_file} ]]; then
  echo "Production release or environment file is missing." >&2
  exit 1
fi

chmod 0700 "${work_dir}"
cd "${release_dir}"

docker compose --env-file "${env_file}" -f docker-compose.production.yml \
  exec -T mongo sh -c \
  'exec mongodump --quiet --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --db "$MONGO_DB" --archive --gzip' \
  > "${work_dir}/mongo.archive.gz"

mkdir -p "${work_dir}/minio"
docker compose --env-file "${env_file}" -f docker-compose.production.yml \
  run --rm -T \
  -v "${work_dir}/minio:/backup" \
  --entrypoint /bin/sh minio-init -c \
  'mc alias set source http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc mirror --overwrite "source/$MINIO_BUCKET" /backup'

tar -C "${root_dir}/shared" -czf "${work_dir}/workflows.tar.gz" workflows
install -m 0600 "${env_file}" "${work_dir}/env.production"

docker compose --env-file "${env_file}" -f docker-compose.production.yml \
  stop caddy app weaviate redis
stopped=1

docker run --rm \
  -v eurskem-ai_weaviate-data:/source:ro \
  -v "${work_dir}:/backup" \
  alpine:3.22 \
  tar -C /source -czf /backup/weaviate-data.tar.gz .
docker run --rm \
  -v eurskem-ai_redis-data:/source:ro \
  -v "${work_dir}:/backup" \
  alpine:3.22 \
  tar -C /source -czf /backup/redis-data.tar.gz .

tar -C "${work_dir}" -czf "${archive}" .
chmod 0600 "${archive}"
sha256sum "${archive}" > "${archive}.sha256"

docker compose --env-file "${env_file}" -f docker-compose.production.yml up -d
stopped=0
echo "Backup created: ${archive}"
echo "Copy it to encrypted off-server storage; a VPS-local backup is not sufficient."
