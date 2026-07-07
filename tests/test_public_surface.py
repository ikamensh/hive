"""Package surface invariants: public-looking packages need either public
modules or a facade, while wholly internal implementation lives under an
underscore package."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HIVE = ROOT / "hive"


def test_public_packages_have_public_modules_or_facades():
    """A public-looking package should not be a hollow shell of `_*.py` files.

    When a package is all implementation detail, name the package itself with a
    leading underscore. When it is public, expose that interface through
    non-underscore modules or an explicit facade in `__init__.py`.
    """
    offenders: list[str] = []
    for package in sorted(path for path in HIVE.iterdir() if path.is_dir()):
        if package.name.startswith("_"):
            continue
        modules = [
            path
            for path in package.glob("*.py")
            if path.name not in {"__init__.py", "__main__.py"}
        ]
        if not modules:
            continue
        public_modules = [path for path in modules if not path.name.startswith("_")]
        init = package / "__init__.py"
        init_text = init.read_text() if init.exists() else ""
        has_facade = "__all__" in init_text and bool(init_text.strip())
        if not public_modules and not has_facade:
            offenders.append(package.name)

    assert offenders == []
