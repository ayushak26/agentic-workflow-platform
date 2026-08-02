"""Explicit per-scope entity registry — the primary detection mechanism.

Thin wrapper over EntityVault for manual registration (a consultant
declaring a known partner/client/coordinator/acronym before a run) and for
syncing from a finalized ProposalGraph. Auto-detected entities (from the
regex/NER safety net in entity_tokenizer.py) are registered the same way,
just with a different ``source`` label — there is no separate mechanism.

Simplification: a legal name and its acronym are registered as two
independent entities (two placeholders), not unified aliases of one entity.
Both are still fully protected; the only cost is that the same real-world
organisation may appear as two different placeholders in tokenized text,
which is a cosmetic gap, not a confidentiality one. Alias unification is
left for a later phase if it proves necessary.
"""
from __future__ import annotations

from typing import Any

from app.security.entity_vault import EntityVault

ENTITY_TYPES = frozenset(
    {
        "organisation",
        "partner",
        "client",
        "coordinator",
        "project_acronym",
        "person",
        "email",
        "phone",
        "domain",
        "grant_agreement_number",
    }
)


class EntityRegistry:
    def __init__(self, vault: EntityVault) -> None:
        self._vault = vault

    async def register(
        self,
        *,
        session_id: str,
        collection_id: str,
        entity_type: str,
        value: str,
        source: str = "manual",
    ) -> str:
        if entity_type not in ENTITY_TYPES:
            raise ValueError(
                f"unknown entity_type {entity_type!r}; must be one of {sorted(ENTITY_TYPES)}"
            )
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return await self._vault.get_or_create_placeholder(
            session_id=session_id,
            collection_id=collection_id,
            entity_type=entity_type,
            real_value=value,
            source=source,
        )

    async def list_entities(
        self, *, session_id: str, collection_id: str
    ) -> list[dict[str, str]]:
        return await self._vault.list_scope_entities(
            session_id=session_id, collection_id=collection_id
        )

    async def delete(
        self, *, session_id: str, collection_id: str, entity_type: str, value: str
    ) -> bool:
        return await self._vault.delete_entity(
            session_id=session_id,
            collection_id=collection_id,
            entity_type=entity_type,
            value=value,
        )

    async def sync_from_partners(
        self, *, session_id: str, collection_id: str, graph: Any
    ) -> list[str]:
        """Register every partner on a finalized ProposalGraph.

        Provided as a callable utility, not auto-wired into any node — no
        single "partners are now final" hook was confirmed in this codebase.
        Call this explicitly once a proposal's consortium is settled.
        """
        placeholders: list[str] = []
        for partner in getattr(graph, "partners", None) or []:
            legal_name = getattr(partner, "legal_name", None)
            if legal_name:
                placeholders.append(
                    await self.register(
                        session_id=session_id,
                        collection_id=collection_id,
                        entity_type="organisation",
                        value=legal_name,
                        source="proposal_graph_sync",
                    )
                )
            acronym = getattr(partner, "acronym", None)
            if acronym:
                placeholders.append(
                    await self.register(
                        session_id=session_id,
                        collection_id=collection_id,
                        entity_type="organisation",
                        value=acronym,
                        source="proposal_graph_sync",
                    )
                )
        return placeholders
