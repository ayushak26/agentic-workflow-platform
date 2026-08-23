"""Generate three synthetic RFP fixtures for the Phase 2 test corpus.

Uses OpenAI to produce realistic consulting-RFP-style documents in three
distinct industries: mining, finance, manufacturing. Output to tests/fixtures/
as Markdown. Run once; outputs are committed to git.

The point is to bootstrap a corpus with synthetic but plausible documents,
a standard production technique when real client data can't be shared.

Usage:
    docker compose exec -T app python scripts/generate_fixtures.py
"""
from __future__ import annotations

import os
from pathlib import Path

from openai import OpenAI

FIXTURES_DIR = Path("tests/fixtures")
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

CLIENT = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
MODEL = "gpt-4o-mini"  # cheap, fast, plenty good for synthetic content

RFPS = [
    {
        "filename": "rfp_mining_efficiency.md",
        "industry": "mining",
        "client": "A large multi-site iron ore mining company in Western Australia",
        "challenge": (
            "Aging fleet of haul trucks and inconsistent ore-grade variability "
            "across pits, causing missed throughput targets and rising fuel costs. "
            "Wants help diagnosing bottlenecks across the pit-to-port flow and "
            "building a 12-month operational improvement roadmap."
        ),
        "must_include_terms": [
            "ore grade", "haul cycle", "pit-to-port", "throughput", "OEE",
            "fleet utilization", "blast fragmentation", "shovel-truck match",
        ],
    },
    {
        "filename": "rfp_finance_transformation.md",
        "industry": "finance",
        "client": "A mid-size European universal bank consolidating post-trade operations",
        "challenge": (
            "Three separate post-trade settlement platforms (equities, fixed income, "
            "derivatives) with inconsistent reconciliation processes, regulatory "
            "reporting gaps under Basel III/IV requirements, and rising operational "
            "risk costs. Wants a target operating model and 18-month transformation plan."
        ),
        "must_include_terms": [
            "regulatory capital", "Basel III", "post-trade settlement",
            "reconciliation", "operational risk", "RWA", "T+1 settlement",
            "STP rate",
        ],
    },
    {
        "filename": "rfp_manufacturing_digital.md",
        "industry": "manufacturing",
        "client": "A tier-1 automotive parts supplier with 12 plants across North America",
        "challenge": (
            "Plants run different MES vendors with no centralized OEE visibility. "
            "Production-quality data is captured but not analyzed. Unplanned downtime "
            "is rising. Wants a digital manufacturing strategy, MES rationalization "
            "plan, and a predictive-maintenance pilot at two plants."
        ),
        "must_include_terms": [
            "OEE", "MES integration", "predictive maintenance", "unplanned downtime",
            "Andon", "first-pass yield", "takt time", "SCADA",
        ],
    },
]

PROMPT_TEMPLATE = """You are writing a synthetic RFP for a consulting engagement. \
The document is for testing purposes only. Make it realistic but generic, no \
named real companies, no real client names.

Write an RFP document (about 800-1000 words) with this structure:

1. Background and Context - describe the client briefly (use the generic description below)
2. Current State Challenges - 4-5 specific operational pain points
3. Desired Outcomes - what success looks like
4. Scope of Engagement - bullet list of work streams
5. Selection Criteria - what they're looking for in a consulting partner
6. Timeline and Constraints

CLIENT DESCRIPTION:
{client}

CORE CHALLENGE:
{challenge}

INDUSTRY: {industry}

The document must naturally incorporate these industry-specific terms (don't list \
them - use them in context): {terms}.

Output Markdown only. Start with a # heading. Do not include any preamble or \
explanation outside the RFP itself."""


def main() -> None:
    """Compute the main."""
    for spec in RFPS:
        prompt = PROMPT_TEMPLATE.format(
            client=spec["client"],
            challenge=spec["challenge"],
            industry=spec["industry"],
            terms=", ".join(spec["must_include_terms"]),
        )
        print(f"Generating {spec['filename']}...")
        response = CLIENT.chat.completions.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or ""

        output_path = FIXTURES_DIR / spec["filename"]
        output_path.write_text(content)
        print(f"  written: {output_path} ({len(content)} chars)")

    print("\nAll three fixtures generated.")


if __name__ == "__main__":
    main()