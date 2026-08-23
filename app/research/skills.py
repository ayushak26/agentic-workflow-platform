"""Allowlisted adapter for K-Dense Scientific Agent Skills.

The upstream repository contains Agent Skills instructions, scripts, assets,
and references. This platform intentionally consumes only reviewed SKILL.md
guidance. It never executes skill scripts or grants the tools declared in
skill frontmatter. That keeps workflow execution inside the platform's normal
node, RBAC, audit, cost, and data-isolation boundaries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9-]{2,}")
_MAX_SKILL_FILE_BYTES = 1_000_000
# Excluded from scoring token overlap so two unrelated texts sharing only
# common English words (e.g. a skill description and a long research
# question both containing "and"/"the"/"for") don't accrue a coincidental
# match score that competes with genuine domain-term overlap.
_STOPWORD_TOKENS = frozenset(
    {
        "and", "the", "for", "with", "from", "that", "this", "into",
        "use", "used", "using", "any", "other", "such", "than", "then",
        "when", "where", "which", "while", "each", "every", "these",
        "those", "you", "your", "not", "but", "are", "was", "were",
        "will", "can", "may", "its", "own", "all", "including",
    }
)
_BLOCKED_SECTION_PHRASES = (
    "dependencies",
    "external resources",
    "visual enhancement",
    "integration with other skills",
    "install",
    "setup",
)
_RISKY_COMMAND_PREFIXES = (
    "curl ",
    "wget ",
    "pip install",
    "uv pip install",
    "npm install",
    "npx ",
    "brew install",
    "apt-get ",
    "apk add",
)
_DOMAIN_HINTS: dict[str, set[str]] = {
    "literature-review": {
        "literature",
        "review",
        "papers",
        "evidence",
        "state-of-the-art",
        "synthesis",
    },
    "scientific-writing": {
        "write",
        "writing",
        "paper",
        "methodology",
        "results",
        "discussion",
    },
    "research-grants": {
        "grant",
        "proposal",
        "funding",
        "impact",
        "objectives",
        "work-package",
    },
    "scientific-critical-thinking": {
        "critical",
        "evaluate",
        "assumption",
        "limitations",
        "bias",
        "validity",
    },
    "citation-management": {
        "citation",
        "references",
        "bibliography",
        "doi",
    },
    "paper-lookup": {
        "paper",
        "lookup",
        "doi",
        "pubmed",
        "arxiv",
    },
    "research-lookup": {
        "research",
        "sources",
        "verified",
        "compile",
        "state-of-art",
        "baseline",
    },
    "database-lookup": {
        "database",
        "dataset",
        "records",
        "query",
    },
    "geomaster": {
        "territorial",
        "land-use",
        "spatial",
        "geospatial",
        "earth-observation",
        "remote-sensing",
    },
    "geopandas": {
        "territorial",
        "spatial",
        "pilot-region",
        "leakage",
        "geospatial",
        "mapping",
    },
    "pymoo": {
        "optimisation",
        "optimization",
        "trade-off",
        "pareto",
        "multi-objective",
        "scenario",
    },
    "networkx": {
        "network",
        "value-chain",
        "actor",
        "relationship",
        "traceability",
        "graph",
    },
    "hypothesis-generation": {
        "hypothesis",
        "falsifiable",
        "testable",
        "prediction",
    },
    "statistical-analysis": {
        "statistical",
        "statistics",
        "validation",
        "model",
        "significance",
        "uncertainty",
    },
}


@dataclass(frozen=True)
class SkillDocument:
    """Provides the SkillDocument behaviour.

    Attributes:
        name (str).
        description (str).
        version (str).
        license (str).
        instructions (str).
    """
    name: str
    description: str
    version: str
    license: str
    instructions: str

    def metadata(self) -> dict[str, str]:
        """Compute the metadata.

        Returns:
            dict[str, str]: The result.
        """
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "license": self.license,
        }


@dataclass(frozen=True)
class SkillSelection:
    """Provides the SkillSelection behaviour.

    Attributes:
        skills (tuple[SkillDocument, ...]).
        reason (str).
    """
    skills: tuple[SkillDocument, ...]
    reason: str

    @property
    def names(self) -> list[str]:
        """The names."""
        return [skill.name for skill in self.skills]

    @property
    def versions(self) -> dict[str, str]:
        """The versions."""
        return {skill.name: skill.version for skill in self.skills}


class ScientificSkillCatalog:
    """Read and select only configured, locally installed skill documents."""

    def __init__(
        self,
        root: Path,
        *,
        allowlist: Iterable[str],
        enabled: bool = True,
        max_prompt_chars: int = 30_000,
    ) -> None:
        """Initialize the ScientificSkillCatalog.

        Args:
            root (Path): The root.
            allowlist (Iterable[str]): The allowlist.
            enabled (bool): The enabled (optional, default True).
            max_prompt_chars (int): The max prompt chars (optional, default 30000).
        """
        self.root = root.expanduser()
        self.enabled = enabled
        self.allowlist = tuple(dict.fromkeys(allowlist))
        self.max_prompt_chars = max(1000, max_prompt_chars)
        self._skills: dict[str, SkillDocument] = {}
        self._load_errors: dict[str, str] = {}

    def refresh(self) -> None:
        """Refresh the result."""
        self._skills.clear()
        self._load_errors.clear()
        if not self.enabled:
            return
        if not self.root.is_dir():
            self._load_errors["__catalog__"] = "skills directory not found"
            return

        root = self.root.resolve()
        for name in self.allowlist:
            try:
                if not _SAFE_NAME.fullmatch(name):
                    raise ValueError("invalid skill name")
                path = (root / name / "SKILL.md").resolve()
                if root not in path.parents:
                    raise ValueError("skill path escapes configured root")
                if not path.is_file():
                    raise FileNotFoundError("SKILL.md not found")
                if path.stat().st_size > _MAX_SKILL_FILE_BYTES:
                    raise ValueError("SKILL.md exceeds size limit")
                document = _parse_skill(path.read_text(encoding="utf-8"))
                if document.name != name:
                    raise ValueError(
                        "frontmatter name does not match directory"
                    )
                self._skills[name] = document
            except Exception as exc:
                self._load_errors[name] = (
                    f"{type(exc).__name__}: {exc}"
                )

    async def probe(self) -> bool:
        """Probe the result.

        Returns:
            bool: The result.
        """
        return (
            self.enabled
            and bool(self._skills)
            and not self._load_errors
        )

    def metadata(self) -> list[dict[str, str]]:
        """Compute the metadata.

        Returns:
            list[dict[str, str]]: The result.
        """
        return [
            self._skills[name].metadata()
            for name in self.allowlist
            if name in self._skills
        ]

    @property
    def load_errors(self) -> dict[str, str]:
        """The load errors."""
        return dict(self._load_errors)

    @property
    def loaded_skill_names(self) -> tuple[str, ...]:
        """Allowlisted skills whose SKILL.md actually parsed successfully.

        Narrower than ``allowlist`` — a name can be allowlisted but still
        fail to load (missing file, oversized, malformed frontmatter). Safe
        to use for building a ``requested=`` list for select() without
        risking a ValueError from an allowlisted-but-unloaded name.
        """
        return tuple(self._skills)

    def select(
        self,
        *,
        objective: str,
        requested: Iterable[str] = (),
        auto_select: bool = True,
        max_skills: int = 3,
    ) -> SkillSelection:
        """Select the result.

        Args:
            objective (str): The objective.
            requested (Iterable[str]): The requested (optional, default ()).
            auto_select (bool): The auto select (optional, default True).
            max_skills (int): The max skills (optional, default 3).

        Returns:
            SkillSelection: The result.
        """
        if not self.enabled:
            raise RuntimeError("Scientific Agent Skills are disabled")
        if not self._skills:
            raise RuntimeError(
                "No approved Scientific Agent Skills are available"
            )

        limit = max(1, min(max_skills, 5))
        selected: list[str] = []
        for name in requested:
            if name in selected:
                continue
            if name not in self.allowlist:
                raise ValueError(
                    f"Scientific skill {name!r} is not approved"
                )
            if name not in self._skills:
                raise ValueError(
                    f"Scientific skill {name!r} is not installed"
                )
            selected.append(name)
            if len(selected) >= limit:
                break

        scored: list[tuple[int, str]] = []
        if auto_select and len(selected) < limit:
            for name, skill in self._skills.items():
                if name in selected:
                    continue
                scored.append(
                    (_skill_score(objective, skill), name)
                )
            scored.sort(key=lambda item: (-item[0], item[1]))
            selected.extend(
                name
                for score, name in scored
                if score > 0
            )
            selected = selected[:limit]

        if not selected:
            # Deterministic safe fallback for generic research objectives.
            selected.append(next(iter(self._skills)))

        reason = (
            "explicit selection"
            if requested and not auto_select
            else "explicit selection plus deterministic objective matching"
            if requested
            else "deterministic objective matching"
        )
        return SkillSelection(
            skills=tuple(self._skills[name] for name in selected),
            reason=reason,
        )

    def prompt_bundle(self, selection: SkillSelection) -> str:
        """Compute the prompt bundle.

        Args:
            selection (SkillSelection): The selection.

        Returns:
            str: The bundle.
        """
        chunks: list[str] = []
        remaining = self.max_prompt_chars
        for skill in selection.skills:
            header = (
                f"\n<scientific-skill name={skill.name!r} "
                f"version={skill.version!r}>\n"
            )
            footer = "\n</scientific-skill>\n"
            available = remaining - len(header) - len(footer)
            if available <= 0:
                break
            body = skill.instructions[:available]
            chunks.append(f"{header}{body}{footer}")
            remaining -= len(header) + len(body) + len(footer)
        return "".join(chunks)


def _parse_skill(text: str) -> SkillDocument:
    """Parse the skill.

    Args:
        text (str): The text.

    Returns:
        SkillDocument: The skill.
    """
    if not text.startswith("---"):
        raise ValueError("missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError("invalid YAML frontmatter")
    metadata = yaml.safe_load(parts[1]) or {}
    if not isinstance(metadata, dict):
        raise ValueError("frontmatter must be an object")

    name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    license_name = str(metadata.get("license") or "unspecified").strip()
    nested = metadata.get("metadata") or {}
    version = (
        str(nested.get("version") or "unspecified")
        if isinstance(nested, dict)
        else "unspecified"
    )
    if not _SAFE_NAME.fullmatch(name):
        raise ValueError("invalid frontmatter name")
    if not description:
        raise ValueError("missing skill description")

    instructions = _sanitize_instructions(parts[2])
    if not instructions:
        raise ValueError("skill has no usable guidance")
    return SkillDocument(
        name=name,
        description=description,
        version=version,
        license=license_name,
        instructions=instructions,
    )


def _sanitize_instructions(text: str) -> str:
    """Keep research guidance while removing execution/install directions."""

    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    output: list[str] = []
    blocked_level: int | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip().lower()
            if blocked_level is not None and level <= blocked_level:
                blocked_level = None
            if any(phrase in title for phrase in _BLOCKED_SECTION_PHRASES):
                blocked_level = level
                continue
        if blocked_level is not None:
            continue
        if line.strip().lower().startswith(_RISKY_COMMAND_PREFIXES):
            continue
        output.append(line)

    return "\n".join(output).strip()


def _skill_score(objective: str, skill: SkillDocument) -> int:
    """Internal helper for the skill score step.

    Args:
        objective (str): The objective.
        skill (SkillDocument): The skill.

    Returns:
        int: The score.
    """
    objective_tokens = (
        set(_TOKEN.findall(objective.lower())) - _STOPWORD_TOKENS
    )
    corpus_tokens = set(
        _TOKEN.findall(
            f"{skill.name} {skill.description}".lower()
        )
    ) - _STOPWORD_TOKENS
    score = len(objective_tokens & corpus_tokens) * 3
    score += len(
        objective_tokens & _DOMAIN_HINTS.get(skill.name, set())
    ) * 4
    # Skill names are always hyphenated (directory names), and every
    # objective built from _TRACK_GUIDANCE embeds them in that exact form —
    # check it directly rather than only a space-joined variant that never
    # actually occurs anywhere in this codebase.
    name_lower = skill.name.lower()
    if name_lower in objective.lower() or name_lower.replace("-", " ") in objective.lower():
        score += 10
    return score
