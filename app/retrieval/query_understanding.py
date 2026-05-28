"""Stage 1 of the retrieval pipeline: query understanding.

For now this is a pass-through. The pipeline supports query rewrite as a
toggle (RetrievalQuery.rewrite_query), and downstream stages don't depend
on what comes out — so we ship a no-op now and improve it later.

What a real rewrite would do:
  - Extract entities (industry, geography, time range) from the raw query.
  - Reformulate verbose RFP language into search-friendly phrases.
  - Expand acronyms ("TPS Bible" stays, but "FY24 CapEx" becomes
    "fiscal year 2024 capital expenditure").

The Optimoz proposal Q2 step 1 ("Query Understanding") commits to this
capability. For Phase 3 the stub is fine — retrieval works end-to-end and
the LLM call cost is saved until rewriting demonstrably improves recall.
"""
from __future__ import annotations

from app.llm.base import LLMGateway


async def rewrite_query(query: str, llm: LLMGateway) -> str:
    """Return a search-optimized version of the query.

    Stub: returns the input unchanged. The signature accepts an LLMGateway
    so when we wire a real rewrite later, no caller has to change.
    """
    return query