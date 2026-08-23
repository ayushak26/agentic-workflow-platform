#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
socket_path=${SNIPPET_RUNNER_SOCKET_PATH:-/tmp/snippet-runner.sock}
pid_file=${SNIPPET_RUNNER_PID_FILE:-/tmp/eurskem-snippet-runner.pid}
log_file=${SNIPPET_RUNNER_LOG_FILE:-/tmp/eurskem-snippet-runner.log}
python=${PYTHON:-"${repo_root}/.venv/bin/python"}

if [[ ! -x ${python} ]]; then
  echo "Python environment not found at ${python}. Run 'uv sync --frozen --all-extras --dev' first." >&2
  exit 1
fi

if [[ -f ${pid_file} ]]; then
  existing_pid=$(cat "${pid_file}" 2>/dev/null || true)
  if [[ -n ${existing_pid} ]] && kill -0 "${existing_pid}" 2>/dev/null; then
    echo "Snippet runner is already running (pid ${existing_pid}, socket ${socket_path})."
    exit 0
  fi
  rm -f "${pid_file}"
fi

rm -f "${socket_path}"
mkdir -p "$(dirname "${socket_path}")"

cd "${repo_root}"
SNIPPET_RUNNER_SOCKET_PATH="${socket_path}" \
  nohup "${python}" -m app.runtime.snippet_daemon >"${log_file}" 2>&1 &
runner_pid=$!
echo "${runner_pid}" >"${pid_file}"

for _ in {1..50}; do
  [[ -S ${socket_path} ]] && break
  if ! kill -0 "${runner_pid}" 2>/dev/null; then
    echo "Snippet runner exited before creating ${socket_path}." >&2
    cat "${log_file}" >&2 || true
    exit 1
  fi
  sleep 0.1
done

if [[ ! -S ${socket_path} ]]; then
  echo "Snippet runner did not create ${socket_path}." >&2
  exit 1
fi

SNIPPET_RUNNER_SOCKET_PATH="${socket_path}" "${python}" - <<'PY'
import asyncio
import os

from app.runtime.snippet_client import SnippetRunnerClient


async def main() -> None:
    await SnippetRunnerClient(os.environ["SNIPPET_RUNNER_SOCKET_PATH"]).probe()


asyncio.run(main())
PY

echo "Snippet runner ready (pid ${runner_pid}, socket ${socket_path}, log ${log_file})."