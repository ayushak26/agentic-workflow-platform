"""Public TLS, readiness, and security-header smoke test."""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


def request(url: str) -> tuple[int, dict[str, str], bytes]:
    """Compute the request.

    Args:
        url (str): Target URL.

    Returns:
        tuple[int, dict[str, str], bytes]: The result.
    """
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def main() -> None:
    """Compute the main."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if not base.startswith("https://"):
        raise SystemExit("Production smoke test requires an https:// URL")

    health_status, health_headers, _ = request(f"{base}/health")
    ready_status, _, ready_body = request(f"{base}/ready")
    docs_status, _, _ = request(f"{base}/docs")
    metrics_status, _, _ = request(f"{base}/metrics")
    runs_status, _, _ = request(f"{base}/api/runs/mine")
    pipelines_status, _, _ = request(f"{base}/api/pipelines")
    business_status, _, _ = request(
        f"{base}/api/runs/mine/removed-smoke-run/business-projection"
    )

    failures: list[str] = []
    if health_status != 200:
        failures.append(f"/health returned {health_status}")
    if ready_status != 200:
        try:
            detail = json.loads(ready_body)
        except Exception:
            detail = ready_body.decode("utf-8", "replace")[:300]
        failures.append(f"/ready returned {ready_status}: {detail}")
    if docs_status != 404:
        failures.append(f"/docs should be private but returned {docs_status}")
    if metrics_status != 404:
        failures.append(f"/metrics should be private but returned {metrics_status}")
    if runs_status != 401:
        failures.append(
            "/api/runs/mine should be mounted and authentication-protected "
            f"but returned {runs_status}"
        )
    if pipelines_status != 404:
        failures.append(f"/api/pipelines should be removed but returned {pipelines_status}")
    if business_status != 404:
        failures.append(
            "Removed Business run endpoint should return 404 but returned "
            f"{business_status}"
        )
    required_headers = (
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "X-Request-ID",
    )
    missing = [name for name in required_headers if name not in health_headers]
    if missing:
        failures.append("Missing security headers: " + ", ".join(missing))
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Production smoke test passed for {base}.")


if __name__ == "__main__":
    main()
