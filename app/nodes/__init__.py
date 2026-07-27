"""Built-in workflow-node discovery.

Every module in :mod:`app.nodes` is imported once so its
``@NodeRegistry.register`` decorators run. This removes the easy-to-miss
manual step that previously caused valid nodes such as
``ClaimEvidenceVerifier`` to appear as unknown after their module was added
but not imported here.

Import failures are retained for the workflow preflight report instead of
taking down the whole API. A workflow that needs a failed module is blocked
before execution with the exact module/import error.
"""
from __future__ import annotations

from importlib import import_module
from pkgutil import iter_modules

_SKIP_MODULES = {"base", "registry"}
_DISCOVERY_ERRORS: dict[str, str] = {}
_DISCOVERED = False


def discover_nodes(*, force: bool = False) -> dict[str, str]:
    """Import all built-in node modules and return module import failures."""

    global _DISCOVERED
    if _DISCOVERED and not force:
        return dict(_DISCOVERY_ERRORS)

    _DISCOVERY_ERRORS.clear()
    for module in sorted(iter_modules(__path__), key=lambda item: item.name):
        if module.name in _SKIP_MODULES:
            continue
        qualified_name = f"{__name__}.{module.name}"
        try:
            import_module(qualified_name)
        except Exception as exc:
            _DISCOVERY_ERRORS[qualified_name] = (
                f"{type(exc).__name__}: {exc}"
            )

    _DISCOVERED = True
    return dict(_DISCOVERY_ERRORS)


def node_discovery_errors() -> dict[str, str]:
    """Return a copy so callers cannot mutate discovery state."""

    return dict(_DISCOVERY_ERRORS)


discover_nodes()
