"""Business View — the surface a business employee works on, not a run viewer.

    run document + workflow spec + gate + cost ledger
                          │
                          ▼
              build_business_projection()          (projection.py — pure)
                          │
                ┌─────────┼─────────┐
                ▼         ▼         ▼
           activities   status   attention        (business language, no JSON)
                          │
                          ▼
                narrate() / explain()             (optional, validated, cached)
                          │
                          ▼
                    Business View

The technical layer is untouched: node events, per-node outputs and prompts
are still recorded exactly as before, and Cockpit still reads them. This
package consumes their *business projection* (§46).
"""
from app.workflow.business_view.common import humanize_identifier
from app.workflow.business_view.models import (
    BusinessAction,
    BusinessActionType,
    BusinessActivityView,
    BusinessAttentionItem,
    BusinessFact,
    BusinessProjection,
    BusinessSource,
    BusinessStatusView,
)
from app.workflow.business_view.projection import build_business_projection

__all__ = [
    "BusinessAction",
    "BusinessActionType",
    "BusinessActivityView",
    "BusinessAttentionItem",
    "BusinessFact",
    "BusinessProjection",
    "BusinessSource",
    "BusinessStatusView",
    "build_business_projection",
    "humanize_identifier",
]
