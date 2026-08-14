"""Fixture-backed mock for the d365-finance-scm-mcp business-tool layer.

mcp-servers/d365-finance-scm-mcp is a real, generic OData adapter over a live
Dynamics 365 Finance & Operations environment — it deliberately exposes only
low-level tools (erp_query, erp_get_record, ...) and expects a narrow,
business-specific tool layer to be built on top (see its README's "Recommended
business layer" section). That layer, and a live F&O tenant, don't exist yet.

This package is that business-tool layer's *fixture-backed twin*, following
the same pattern as app/mcp/dynamics/ (the separate Dataverse CRM connector):
same tool names and output shapes a real implementation would need, served
from static fixture data so the pump-manufacturer routing workflow can be
built and tested end-to-end today. Swapping in the real F&O-backed tools later
should not require changing the workflow.
"""
