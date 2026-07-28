#!/bin/sh
set -eu

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing "local/$MINIO_BUCKET"
mc admin user add local "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" 2>/dev/null || true
sed "s/__BUCKET__/$MINIO_BUCKET/g" \
  /config/minio-app-policy.json > /tmp/minio-app-policy.json
mc admin policy create local eurskem-app /tmp/minio-app-policy.json 2>/dev/null || true
mc admin policy attach local eurskem-app --user "$MINIO_ACCESS_KEY"
