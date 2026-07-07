"""Isolation contract for the standalone subsystem packages.

hive/{fleet,agents,worker,persistence,llm} are independently usable: anyone
can import one without dragging in the rest of hive. That is a structural
property, so it is enforced structurally — parse every import in each package
and reject hive imports that leave the package. The rest of hive may depend
on these packages; they may not depend back (or on each other), keeping them
leaves of the dependency graph.

The demos double as API contracts: each demo exercises exactly its own
subsystem, so a demo importing another hive package means the public API has
a hole (or the demo drifted).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ISOLATED_PACKAGES = ("fleet", "agents", "worker", "persistence", "llm")


def hive_imports(path: Path) -> set[str]:
    """Every `hive.*` module this file imports, as dotted names."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.split(".")[0] == "hive")
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module.split(".")[0] == "hive":
                found.add(module)
    return found


def test_isolated_packages_import_nothing_else_from_hive():
    violations: list[str] = []
    for package in ISOLATED_PACKAGES:
        allowed_prefix = f"hive.{package}"
        for path in (ROOT / "hive" / package).rglob("*.py"):
            for module in hive_imports(path):
                if module != allowed_prefix and not module.startswith(allowed_prefix + "."):
                    violations.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not violations, "isolated packages must not import the rest of hive:\n" + "\n".join(
        sorted(violations)
    )


def test_each_isolated_package_has_a_facade():
    """The package's `__init__` is the public API: it must exist and declare
    `__all__`, so demos and hive itself import from one blessed surface."""
    for package in ISOLATED_PACKAGES:
        init = ROOT / "hive" / package / "__init__.py"
        assert init.is_file(), f"hive/{package} is missing __init__.py"
        assert "__all__" in init.read_text(), f"hive/{package}/__init__.py declares no __all__"


def test_every_isolated_package_has_two_demos():
    for package in ISOLATED_PACKAGES:
        demo_dir = ROOT / "demos" / package
        demos = sorted(p.name for p in demo_dir.glob("*.py") if not p.name.startswith("_"))
        assert len(demos) >= 2, f"demos/{package} has {demos}; every subsystem ships 2 demos"


def test_demos_use_only_their_own_subsystem():
    """A fleet demo importing hive.agents would mean fleet's API has a hole
    (or the demo shows the wrong thing). Demo helper modules (non-hive local
    imports) are fine."""
    violations: list[str] = []
    for package in ISOLATED_PACKAGES:
        allowed_prefix = f"hive.{package}"
        for path in (ROOT / "demos" / package).glob("*.py"):
            for module in hive_imports(path):
                if module != allowed_prefix and not module.startswith(allowed_prefix + "."):
                    violations.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not violations, "demos must stay inside their subsystem:\n" + "\n".join(
        sorted(violations)
    )
