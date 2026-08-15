"""Business View's projection — see app/workflow/business_view/.

This module used to hold the whole projection. It grew into a package when
Business View stopped being a re-labelling of workflow execution and became
the screen a business employee actually works on: activity aggregation,
attention resolution, provenance, typed actions, status narration and the
technical/business split each needed room of their own.

The import path stays here because it is the one every caller already uses.
"""
from app.workflow.business_view.common import humanize_identifier
from app.workflow.business_view.projection import build_business_projection

__all__ = ["build_business_projection", "humanize_identifier"]
