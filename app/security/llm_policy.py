"""Data classification + OPA policy enforcement for direct provider calls.

Ports the design built earlier for the (now-removed) OmniRoute proxy layer directly into
Eurskem: OPA itself stays a separate, running, language-agnostic service (a policy engine has
no reason to live inside a Node.js LLM proxy) — only the CLIENT calling it moves to Python.
The actual policy — `omniroute/src/lib/policy/opa/policies/eurskem/routing.rego` — is reused
byte-for-byte; its 12/12 `opa test` cases are still the authoritative spec for this behavior.

Lattice: PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED. Effective classification is
max(declared, detected) — automatic PII/entity detection can only RAISE the classification,
never lower a workflow author's explicit declaration.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.llm.errors import LLMPolicyDeniedError
from app.observability.logging import get_logger
from app.security.entity_tokenizer import ProcessingMode

log = get_logger(__name__)

DataClass = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
Backend = Literal["direct", "openrouter", "local", "unknown"]

_LATTICE_ORDER: dict[DataClass, int] = {
    "PUBLIC": 0,
    "INTERNAL": 1,
    "CONFIDENTIAL": 2,
    "RESTRICTED": 3,
}

_PROCESSING_MODE_TO_DATA_CLASS: dict[str, DataClass] = {
    ProcessingMode.PUBLIC.value: "PUBLIC",
    ProcessingMode.PSEUDONYMISED.value: "CONFIDENTIAL",
    ProcessingMode.RESTRICTED_LOCAL.value: "RESTRICTED",
}


def processing_mode_to_data_class(processing_mode: str | None) -> DataClass:
    """Maps Eurskem's per-node ProcessingMode onto the classification lattice.
    `pseudonymised` maps to CONFIDENTIAL, not INTERNAL: the mode exists BECAUSE the
    underlying data is genuinely confidential (consortium partners, grant details, etc.) —
    entity tokenization happens regardless of this value, but the DECLARED classification
    must reflect true sensitivity. An unset/unknown mode fails toward MORE protection
    (CONFIDENTIAL), never toward PUBLIC."""
    if processing_mode is None:
        return "CONFIDENTIAL"
    return _PROCESSING_MODE_TO_DATA_CLASS.get(processing_mode, "CONFIDENTIAL")


def effective_data_class(declared: DataClass, *, entities_detected: bool) -> DataClass:
    """max(declared, detected) — detecting a real entity in the text implies at least
    CONFIDENTIAL sensitivity, regardless of what the workflow declared."""
    if not entities_detected:
        return declared
    detected: DataClass = "CONFIDENTIAL"
    return declared if _LATTICE_ORDER[declared] >= _LATTICE_ORDER[detected] else detected


# Gateway class name -> (provider id, backend). Provider ids match
# EURSKEM_ZDR_APPROVED_PROVIDERS/EURSKEM_DATA_COLLECTING_PROVIDERS entries.
_PROVIDER_BY_GATEWAY: dict[str, tuple[str, Backend]] = {
    "AnthropicGateway": ("anthropic", "direct"),
    "OpenAIGateway": ("openai", "direct"),
    "OpenRouterGateway": ("openrouter", "openrouter"),
    "KimiK3LocalGateway": ("moonshot-local", "local"),
    "GLM5LocalGateway": ("zai-local", "local"),
}


def provider_and_backend_for_gateway(gateway_class_name: str) -> tuple[str, Backend]:
    """Compute the provider and backend for gateway.

    Args:
        gateway_class_name (str): The gateway class name.

    Returns:
        tuple[str, Backend]: The and backend for gateway.
    """
    return _PROVIDER_BY_GATEWAY.get(gateway_class_name, ("unknown", "unknown"))


@dataclass(frozen=True)
class ProviderComplianceMetadata:
    """Provides the ProviderComplianceMetadata behaviour.

    Attributes:
        zdr (bool | None).
        data_collection (bool | None).
    """
    zdr: bool | None
    data_collection: bool | None


def _parse_csv_env(name: str) -> frozenset[str]:
    """Parse the csv env.

    Args:
        name (str): Workflow or resource name.

    Returns:
        frozenset[str]: The csv env.
    """
    raw = os.environ.get(name, "")
    return frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())


def get_provider_compliance_metadata(provider: str | None) -> ProviderComplianceMetadata:
    """Operator-configured ZDR/data-collection facts — a business/legal fact about Eurskem's
    actual vendor agreements, not derivable from code. A provider absent from both env lists
    resolves to (None, None) — "unknown", never assumed safe. See
    EURSKEM_ZDR_APPROVED_PROVIDERS / EURSKEM_DATA_COLLECTING_PROVIDERS."""
    if not provider:
        return ProviderComplianceMetadata(zdr=None, data_collection=None)
    normalized = provider.strip().lower()
    zdr_approved = _parse_csv_env("EURSKEM_ZDR_APPROVED_PROVIDERS")
    data_collecting = _parse_csv_env("EURSKEM_DATA_COLLECTING_PROVIDERS")
    zdr = True if normalized in zdr_approved else (False if normalized in data_collecting else None)
    data_collection = (
        True if normalized in data_collecting else (False if normalized in zdr_approved else None)
    )
    return ProviderComplianceMetadata(zdr=zdr, data_collection=data_collection)


@dataclass(frozen=True)
class PolicyDecision:
    """Provides the PolicyDecision behaviour.

    Attributes:
        allow (bool).
        reason_codes (tuple[str, ...]).
        constraints (dict[str, bool]).
        policy_version (str | None).
    """
    allow: bool
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    constraints: dict[str, bool] = field(default_factory=dict)
    policy_version: str | None = None


class OpaClient:
    """Minimal OPA REST client — mirrors omniroute/src/lib/policy/opa/client.ts exactly
    (same endpoint shape, same fail-closed contract). Deliberately raises on every failure
    mode (unreachable, timeout, non-2xx, malformed response) rather than returning a default
    verdict — a policy decision that cannot be made is not a decision that can default to
    allow. See docs/architecture/LLM_POLICY_OPA.md."""

    def __init__(
        self,
        *,
        opa_url: str | None = None,
        policy_path: str | None = None,
        timeout_seconds: float = 2.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize the OpaClient.

        Args:
            opa_url (str | None): The opa url (optional, default None).
            policy_path (str | None): The policy path (optional, default None).
            timeout_seconds (float): Timeout in seconds (optional, default 2.0).
            client (httpx.AsyncClient | None): Client instance (optional, default None).
        """
        self._opa_url = (opa_url or os.environ.get("OPA_URL") or "http://localhost:8181").rstrip("/")
        self._policy_path = policy_path or os.environ.get("OPA_POLICY_PATH") or "eurskem/routing/decision"
        self._timeout = timeout_seconds
        self._client = client

    async def evaluate(self, policy_input: dict) -> PolicyDecision:
        """Compute the evaluate.

        Args:
            policy_input (dict): The policy input.

        Returns:
            PolicyDecision: The result.
        """
        url = f"{self._opa_url}/v1/data/{self._policy_path}"
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            response = await client.post(url, json={"input": policy_input})
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OPA request failed ({url}): {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise RuntimeError(f"OPA returned HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"OPA response was not valid JSON: {exc}") from exc

        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict) or not isinstance(result.get("allow"), bool):
            raise RuntimeError("OPA response missing a valid result.allow boolean")

        reason_codes = result.get("reason_codes")
        return PolicyDecision(
            allow=result["allow"],
            reason_codes=tuple(reason_codes) if isinstance(reason_codes, list) else (),
            constraints=result.get("constraints") or {},
            policy_version=result.get("policy_version"),
        )


_default_client: OpaClient | None = None


def get_default_opa_client() -> OpaClient:
    """Return the default opa client.

    Returns:
        OpaClient: The default opa client.
    """
    global _default_client
    if _default_client is None:
        _default_client = OpaClient()
    return _default_client


async def enforce_policy(
    *,
    workspace_id: str,
    data_class: DataClass,
    gateway_class_name: str,
    model: str,
    capabilities: list[str] | None = None,
    opa_client: OpaClient | None = None,
) -> PolicyDecision:
    """Evaluates the routing policy for one candidate (provider, model) pair. Raises
    LLMPolicyDeniedError when denied — callers in a fallback loop should catch this and try
    the next candidate, only propagating it once every candidate has been denied/exhausted."""
    provider, backend = provider_and_backend_for_gateway(gateway_class_name)
    compliance = get_provider_compliance_metadata(provider)
    policy_input = {
        "workspace": workspace_id,
        "data_class": data_class,
        "request": {"capabilities": capabilities or []},
        "model": {"id": model, "family": None},
        "route": {
            "backend": backend,
            "provider": provider,
            "zdr": compliance.zdr,
            "data_collection": compliance.data_collection,
        },
    }
    client = opa_client or get_default_opa_client()
    decision = await client.evaluate(policy_input)
    if not decision.allow:
        log.warning(
            "llm_policy.denied",
            data_class=data_class,
            provider=provider,
            model=model,
            reason_codes=decision.reason_codes,
        )
        raise LLMPolicyDeniedError(
            f"Policy denied route provider={provider!r} model={model!r} for "
            f"data_class={data_class}: {', '.join(decision.reason_codes) or 'denied'}"
        )
    return decision
