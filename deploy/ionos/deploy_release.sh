#!/usr/bin/env bash
set -euo pipefail

release_dir=${1:?release directory is required}
release_sha=${2:?release SHA is required}
root_dir=/opt/eurskem
shared_dir=${root_dir}/shared
env_file=${shared_dir}/.env.production
previous_dir=""

if [[ ! -f ${env_file} ]]; then
  echo "Missing ${env_file}; generate and install it with mode 0600." >&2
  exit 1
fi
if [[ -L ${root_dir}/current ]]; then
  previous_dir=$(readlink -f "${root_dir}/current")
fi
if [[ ! -d ${release_dir} ]]; then
  echo "Release directory does not exist: ${release_dir}" >&2
  exit 1
fi

chmod 600 "${env_file}"
ln -sfn "${env_file}" "${release_dir}/.env.production"
mkdir -p "${shared_dir}/workflows"
rsync -a --ignore-existing --chmod=D2770,F0660 \
  "${release_dir}/workflows/" "${shared_dir}/workflows/"

cd "${release_dir}"
python3 scripts/production_preflight.py --env-file "${env_file}"
docker compose \
  --env-file "${env_file}" \
  -f docker-compose.production.yml \
  config --quiet
docker compose \
  --env-file "${env_file}" \
  -f docker-compose.production.yml \
  build --pull
docker compose \
  --env-file "${env_file}" \
  -f docker-compose.production.yml \
  up -d --remove-orphans

ready=0
for _ in $(seq 1 60); do
  if docker compose \
    --env-file "${env_file}" \
    -f docker-compose.production.yml \
    exec -T app \
    python -c \
      "import urllib.request; from app.config import settings; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/ready', headers={'Host': settings.allowed_hosts[0]}), timeout=5)" \
    >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 5
done

if [[ ${ready} -ne 1 ]]; then
  docker compose \
    --env-file "${env_file}" \
    -f docker-compose.production.yml \
    logs --tail=200
  if [[ -n ${previous_dir} && -d ${previous_dir} ]]; then
    cd "${previous_dir}"
    docker compose \
      --env-file "${env_file}" \
      -f docker-compose.production.yml \
      build
    docker compose \
      --env-file "${env_file}" \
      -f docker-compose.production.yml \
      up -d --remove-orphans
  fi
  echo "Release failed readiness; previous release was restored." >&2
  exit 1
fi

docker compose \
  --env-file "${env_file}" \
  -f docker-compose.production.yml \
  exec -T app \
  python scripts/load_test.py \
    --base-url http://127.0.0.1:8000 \
    --concurrency 100 \
    --requests 100

ln -sfn "${release_dir}" "${root_dir}/current"
printf '%s\n' "${release_sha}" > "${shared_dir}/deployed-sha"
echo "Deployed ${release_sha} from ${release_dir}."
