"""Architecture gates that do not require a third-party import-linter."""

import ast
from pathlib import Path

_FORBIDDEN_ROOTS = {"oc8_enterprise", "oc8_cloud"}
_COMMUNITY_SOURCE = Path(__file__).resolve().parents[2] / "src" / "oc8"


def _forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules = [node.module]
        for module in modules:
            if module.split(".", 1)[0] in _FORBIDDEN_ROOTS:
                violations.append(f"{path.relative_to(_COMMUNITY_SOURCE)}:{node.lineno}: {module}")
    return violations


def test_community_does_not_import_private_edition_or_cloud_packages() -> None:
    violations = [
        violation
        for path in _COMMUNITY_SOURCE.rglob("*.py")
        for violation in _forbidden_imports(path)
    ]

    assert not violations, "Community import boundary violations:\n" + "\n".join(violations)
