"""Dataverse Web API client and its fixture-backed twin.

Two backends behind one interface:

    DynamicsBackend
        ├─ DataverseClient   real Dynamics 365, Entra ID client credentials
        └─ FixtureBackend    the same tool contracts over a fixture store

The mock exists so a workflow can be built and demonstrated without a live
tenant, and — this is the part that matters — so the workflow built against it is
*byte-identical* to the one that runs against production. Only the connection
changes. A demo that hardcodes fake CRM data into the workflow teaches the
audience nothing about the real integration; this teaches them everything except
the tenant.

Authentication (see §24): the reference implementation uses Entra ID **client
credentials** — an application identity. That is right for unattended server-side
triage, which is what this platform does: there is no signed-in user at 3am when
an email arrives. It is *wrong* for anything that must respect a specific user's
CRM record-level permissions, because the application identity sees whatever its
security role grants regardless of who triggered the run. The consequence is that
least privilege has to be enforced on the application user's security role (§25),
not assumed from the caller. Documented in docs/DYNAMICS_365_MCP.md.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from app.mcp.dynamics import odata

API_VERSION = "v9.2"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

#: Refresh this long before the token actually expires, so a call started just
#: under the wire does not fail mid-flight.
TOKEN_REFRESH_MARGIN_SECONDS = 120


class DynamicsError(RuntimeError):
    """A CRM call failed in a way worth describing to a workflow author."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "DYNAMICS_ERROR",
        retryable: bool = False,
        suggested_action: str = "",
    ):
        self.code = code
        self.retryable = retryable
        self.suggested_action = suggested_action
        super().__init__(message)

    def as_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "suggested_action": self.suggested_action,
        }


class DynamicsAuthError(DynamicsError):
    def __init__(self, message: str):
        super().__init__(
            message,
            code="DYNAMICS_AUTH_FAILED",
            retryable=False,
            suggested_action=(
                "Check the Dynamics connection's client id, secret and tenant "
                "in the deployment configuration."
            ),
        )


class DynamicsBackend(ABC):
    """What the MCP server needs from a CRM backend."""

    is_mock: bool = False

    @abstractmethod
    async def whoami(self) -> dict[str, Any]:
        ...

    @abstractmethod
    async def query(
        self,
        entity_set: str,
        *,
        select: list[str],
        filter_expression: str | None = None,
        order_by: str | None = None,
        top: int | None = None,
        expand: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    async def get(
        self, entity_set: str, record_id: str, *, select: list[str]
    ) -> dict[str, Any] | None:
        ...

    @abstractmethod
    async def create(self, entity_set: str, payload: dict[str, Any]) -> str:
        ...

    @abstractmethod
    async def update(
        self, entity_set: str, record_id: str, payload: dict[str, Any]
    ) -> None:
        ...

    async def close(self) -> None:
        return None


class DataverseClient(DynamicsBackend):
    """Live Dynamics 365 over the Dataverse Web API."""

    def __init__(
        self,
        *,
        base_url: str,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._client = http_client
        self._owns_client = http_client is None
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # -- authentication --------------------------------------------------

    async def _access_token(self) -> str:
        """Client-credentials token for the organisation's `.default` scope.

        Cached until shortly before expiry. Acquired against the token endpoint
        directly rather than through a library, so the platform has one HTTP
        stack and one place where a credential can appear.
        """
        now = time.monotonic()
        if self._token and now < self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
            return self._token

        if not (self.tenant_id and self.client_id and self.client_secret):
            raise DynamicsAuthError(
                "The Dynamics connection is missing credentials. Client id, "
                "client secret and tenant id are supplied by the deployment, "
                "never by a workflow."
            )

        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        try:
            response = await self._http().post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": f"{self.base_url}/.default",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise DynamicsError(
                f"Could not reach Microsoft Entra ID: {error}",
                code="DYNAMICS_AUTH_UNREACHABLE",
                retryable=True,
                suggested_action="Check network access from the platform to login.microsoftonline.com.",
            ) from error

        if response.status_code != 200:
            # The response body of a failed token request can contain the
            # correlation id and the AADSTS code, which is what an operator
            # needs — but never the secret, so it is safe to surface.
            raise DynamicsAuthError(
                f"Entra ID rejected the credentials ({response.status_code}): "
                f"{_safe_error_text(response)}"
            )

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise DynamicsAuthError("Entra ID returned no access token.")
        self._token = token
        self._token_expires_at = now + float(payload.get("expires_in", 3600))
        return token

    async def _headers(self, *, write: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {await self._access_token()}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        }
        if write:
            headers["Content-Type"] = "application/json"
            # Return the created/updated record's identity rather than a bare
            # 204, so a create can report the id it made without a second read.
            headers["Prefer"] = "return=representation"
        return headers

    # -- requests --------------------------------------------------------

    def _url(self, path: str, params: dict[str, str] | None = None) -> str:
        url = f"{self.base_url}/api/data/{API_VERSION}/{path}"
        if params:
            url = f"{url}?{odata.encode_params(params)}"
        return url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        write: bool = False,
    ) -> httpx.Response:
        url = self._url(path, params)
        try:
            response = await self._http().request(
                method,
                url,
                headers=await self._headers(write=write),
                json=json_body,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise DynamicsError(
                f"Dynamics did not respond: {error}",
                code="DYNAMICS_UNREACHABLE",
                # A read may be retried freely. A write that timed out has an
                # unknown outcome; the caller's write ledger is what decides
                # whether to retry, so this only reports the fact.
                retryable=not write,
                suggested_action="Check the Dynamics URL and network access.",
            ) from error

        if response.status_code in (401, 403):
            raise DynamicsError(
                f"Dynamics refused the request ({response.status_code}): "
                f"{_safe_error_text(response)}",
                code="DYNAMICS_FORBIDDEN",
                retryable=False,
                suggested_action=(
                    "The application user's security role may not grant access "
                    "to this entity. Check least-privilege configuration."
                ),
            )
        if response.status_code == 429:
            raise DynamicsError(
                "Dynamics is rate limiting this connection.",
                code="DYNAMICS_RATE_LIMITED",
                retryable=True,
                suggested_action="Reduce request volume or retry shortly.",
            )
        if response.status_code >= 400:
            raise DynamicsError(
                f"Dynamics rejected the request ({response.status_code}): "
                f"{_safe_error_text(response)}",
                code="DYNAMICS_REQUEST_REJECTED",
                retryable=response.status_code >= 500 and not write,
            )
        return response

    async def whoami(self) -> dict[str, Any]:
        response = await self._request("GET", "WhoAmI")
        identity = response.json()
        user_id = identity.get("UserId")
        full_name = ""
        if user_id:
            user = await self.get(
                "systemusers", user_id, select=["fullname", "systemuserid"]
            )
            full_name = (user or {}).get("fullname", "")
        return {
            "user_id": user_id or "",
            "full_name": full_name or "unknown",
            "business_unit_id": identity.get("BusinessUnitId"),
            "organization_id": identity.get("OrganizationId"),
        }

    async def query(
        self,
        entity_set: str,
        *,
        select: list[str],
        filter_expression: str | None = None,
        order_by: str | None = None,
        top: int | None = None,
        expand: str | None = None,
    ) -> list[dict[str, Any]]:
        params = odata.build_query(
            select=select,
            filter_expression=filter_expression,
            order_by=order_by,
            top=top,
            expand=expand,
        )
        response = await self._request(
            "GET", odata.entity_path(entity_set), params=params
        )
        return list(response.json().get("value") or [])

    async def get(
        self, entity_set: str, record_id: str, *, select: list[str]
    ) -> dict[str, Any] | None:
        params = odata.build_query(select=select)
        try:
            response = await self._request(
                "GET", odata.entity_path(entity_set, record_id), params=params
            )
        except DynamicsError as error:
            if error.code == "DYNAMICS_REQUEST_REJECTED" and "404" in str(error):
                return None
            raise
        return response.json()

    async def create(self, entity_set: str, payload: dict[str, Any]) -> str:
        response = await self._request(
            "POST", odata.entity_path(entity_set), json_body=payload, write=True
        )
        body = response.json() if response.content else {}
        # `Prefer: return=representation` gives the record back; fall back to
        # the OData-EntityId header, which is what a bare 204 supplies.
        for key, value in body.items():
            if key.endswith("id") and isinstance(value, str):
                return value
        entity_id = response.headers.get("OData-EntityId", "")
        if "(" in entity_id:
            return entity_id.rsplit("(", 1)[-1].rstrip(")")
        raise DynamicsError(
            "Dynamics created the record but returned no identifier.",
            code="DYNAMICS_NO_RECORD_ID",
            retryable=False,
            suggested_action="Check the record in Dynamics before retrying — it may exist.",
        )

    async def update(
        self, entity_set: str, record_id: str, payload: dict[str, Any]
    ) -> None:
        await self._request(
            "PATCH",
            odata.entity_path(entity_set, record_id),
            json_body=payload,
            write=True,
        )


def _safe_error_text(response: httpx.Response) -> str:
    """Extract Dataverse's error message without echoing an entire payload."""
    try:
        body = response.json()
    except Exception:
        return response.text[:300]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message", ""))[:300]
    return str(body)[:300]


class FixtureBackend(DynamicsBackend):
    """The same contracts, served from a fixture file.

    Implements the same query surface (`$select`, `$filter`, `$top`,
    `$orderby`) closely enough that a workflow built here behaves the same way
    against a live tenant — filters are interpreted, not ignored. Writes append
    to the in-memory store so a create returns a real id and a follow-up read
    finds it, which is what makes an end-to-end demo honest.
    """

    is_mock = True

    def __init__(self, fixtures: dict[str, list[dict[str, Any]]] | None = None):
        self.store: dict[str, list[dict[str, Any]]] = {
            key: [dict(item) for item in value]
            for key, value in (fixtures or {}).items()
        }
        self.writes: list[dict[str, Any]] = []
        self._counter = 0

    @classmethod
    def from_file(cls, path: str | Path) -> "FixtureBackend":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data)

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        # A GUID-shaped id, so downstream GUID validation behaves exactly as it
        # would against a real tenant.
        return f"{prefix:0>8}-0000-4000-8000-{self._counter:012d}"

    async def whoami(self) -> dict[str, Any]:
        return {
            "user_id": "00000000-0000-4000-8000-000000000001",
            "full_name": "Demo Integration User",
            "business_unit_id": "00000000-0000-4000-8000-000000000002",
            "organization_id": "00000000-0000-4000-8000-000000000003",
        }

    async def query(
        self,
        entity_set: str,
        *,
        select: list[str],
        filter_expression: str | None = None,
        order_by: str | None = None,
        top: int | None = None,
        expand: str | None = None,
    ) -> list[dict[str, Any]]:
        del expand
        rows = list(self.store.get(entity_set, []))
        if filter_expression:
            rows = [row for row in rows if _fixture_matches(row, filter_expression)]
        if order_by:
            column, _, direction = order_by.partition(" ")
            rows.sort(
                key=lambda row: (row.get(column) is None, row.get(column) or ""),
                reverse=direction.strip().lower() == "desc",
            )
        if top is not None:
            rows = rows[:top]
        return [_project(row, select) for row in rows]

    async def get(
        self, entity_set: str, record_id: str, *, select: list[str]
    ) -> dict[str, Any] | None:
        key = f"{entity_set.rstrip('s')}id"
        for row in self.store.get(entity_set, []):
            if str(row.get(key, "")).lower() == record_id.lower():
                return _project(row, select)
        return None

    async def create(self, entity_set: str, payload: dict[str, Any]) -> str:
        key = f"{entity_set.rstrip('s')}id"
        record_id = self._next_id("f1")
        record = {**payload, key: record_id}
        self.store.setdefault(entity_set, []).append(record)
        self.writes.append(
            {"operation": "create", "entity_set": entity_set, "id": record_id}
        )
        return record_id

    async def update(
        self, entity_set: str, record_id: str, payload: dict[str, Any]
    ) -> None:
        key = f"{entity_set.rstrip('s')}id"
        for row in self.store.get(entity_set, []):
            if str(row.get(key, "")).lower() == record_id.lower():
                row.update(payload)
                self.writes.append(
                    {
                        "operation": "update",
                        "entity_set": entity_set,
                        "id": record_id,
                        "fields": sorted(payload),
                    }
                )
                return
        raise DynamicsError(
            f"No {entity_set} record with id {record_id}.",
            code="DYNAMICS_RECORD_NOT_FOUND",
            retryable=False,
            suggested_action="Look the record up first and map its id.",
        )


def _project(row: dict[str, Any], select: list[str]) -> dict[str, Any]:
    """Mimic `$select`: a fixture row returns only the requested columns."""
    if not select:
        return dict(row)
    return {name: row.get(name) for name in select if name in row or True}


def _fixture_matches(row: dict[str, Any], expression: str) -> bool:
    """Interpret the small OData subset this package generates.

    Deliberately supports exactly the shapes `odata.py` can build — `eq` on a
    string or GUID, `contains(...)`, and `and`/`or` groupings. Anything else
    raises rather than silently matching everything, so a fixture backend can
    never make a filter *look* like it worked when it did not.
    """
    import re

    expression = expression.strip()
    if expression.startswith("(") and expression.endswith(")"):
        # Only strip when the outer parentheses actually wrap the whole thing.
        depth = 0
        wraps = True
        for index, char in enumerate(expression):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(expression) - 1:
                    wraps = False
                    break
        if wraps:
            return _fixture_matches(row, expression[1:-1])

    for joiner, combine in ((" or ", any), (" and ", all)):
        parts = _split_top_level(expression, joiner)
        if len(parts) > 1:
            return combine(_fixture_matches(row, part) for part in parts)

    contains = re.fullmatch(r"contains\(([\w_]+),'(.*)'\)", expression, re.DOTALL)
    if contains:
        column, value = contains.group(1), contains.group(2).replace("''", "'")
        return value.lower() in str(row.get(column) or "").lower()

    equality = re.fullmatch(r"([\w_]+) eq '(.*)'", expression, re.DOTALL)
    if equality:
        column, value = equality.group(1), equality.group(2).replace("''", "'")
        return str(row.get(column) or "").lower() == value.lower()

    guid_equality = re.fullmatch(r"([\w_]+) eq ([0-9a-fA-F-]{36})", expression)
    if guid_equality:
        column, value = guid_equality.group(1), guid_equality.group(2)
        return str(row.get(column) or "").lower() == value.lower()

    number_equality = re.fullmatch(r"([\w_]+) eq (-?\d+)", expression)
    if number_equality:
        column, value = number_equality.group(1), int(number_equality.group(2))
        return row.get(column) == value

    raise DynamicsError(
        f"The demo CRM backend cannot interpret the filter {expression!r}.",
        code="DYNAMICS_MOCK_UNSUPPORTED_FILTER",
        retryable=False,
        suggested_action="This is a platform bug; report the filter shape.",
    )


def _split_top_level(expression: str, joiner: str) -> list[str]:
    """Split on a joiner that is not inside parentheses or a string literal."""
    parts: list[str] = []
    depth = 0
    in_string = False
    current: list[str] = []
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            in_string = not in_string
        elif not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif (
                depth == 0
                and expression[index : index + len(joiner)] == joiner
            ):
                parts.append("".join(current))
                current = []
                index += len(joiner)
                continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    return [part for part in parts if part.strip()]
