"""Character-count helper for the proposal-size guardrails.

``HorizonHTMLProposalRenderer``/``HorizonDOCXProposalRenderer`` reject content
above ``max_content_characters`` to catch a runaway or duplicated draft
before it reaches the page-limit-sensitive renderer. Embedded figures are
spliced in as ``data:image/...;base64,...`` URIs (``app/nodes/figure_embedder.py``)
before that guard runs, so a handful of legitimate diagrams can inflate the
same string by well over a million characters of base64 — bulk already
bounded separately by each renderer's own ``max_embedded_image_bytes`` check.
Counting narrative length excludes that image payload so the guard measures
what it was actually meant to measure: prose, not embedded binary data.
"""
from __future__ import annotations

import re

_DATA_URI_PATTERN = re.compile(r"data:image/[\w.+-]+;base64,[A-Za-z0-9+/=]+")


def narrative_char_count(content: str) -> int:
    """Return `content`'s length with embedded base64 image data URIs excluded."""

    return len(_DATA_URI_PATTERN.sub("", content))
