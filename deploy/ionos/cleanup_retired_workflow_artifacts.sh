#!/usr/bin/env bash
set -euo pipefail

workflows_dir=${1:?workflow directory is required}

if [[ ! -d ${workflows_dir} ]]; then
  exit 0
fi

# Pipeline was a separate product with its own directory and *.pipeline.yaml
# contract. Remove only those artifacts; ordinary Workflow YAML, operator files,
# and Builder state remain untouched.
rm -rf -- "${workflows_dir}/pipelines"
find "${workflows_dir}" -type f -name '*.pipeline.yaml' -delete