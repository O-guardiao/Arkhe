"""
Layer boundary checker — garante que imports respeitem a hierarquia de camadas.

Camadas (de baixo para cima):
  L0: rlm.core.*          (sem dependência de gateway/server/cli/daemon)
  L1: rlm.gateway.*       (pode importar core)
  L2: rlm.server.*        (pode importar core, gateway)
  L3: rlm.daemon.*        (pode importar core, gateway, server)
  L4: rlm.cli.*           (pode importar tudo)

Violações detectadas:
  - core importando gateway, server, cli, daemon
  - gateway importando server, cli, daemon
  - server importando cli, daemon
  - daemon importando cli

Uso:
  uv run python scripts/check_layer_imports.py
  Retorna exit code 1 se houver violações.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Módulos e o que NÃO podem importar (imports proibidos)
FORBIDDEN: dict[str, set[str]] = {
    "rlm.core": {"rlm.gateway", "rlm.server", "rlm.daemon", "rlm.cli"},
    "rlm.gateway": {"rlm.server", "rlm.daemon", "rlm.cli"},
    "rlm.server": {"rlm.daemon", "rlm.cli"},
    "rlm.daemon": {"rlm.cli"},
}


def _layer_of(filepath: Path) -> str | None:
    """Retorna o prefixo de camada do arquivo, ou None se fora do escopo."""
    parts = filepath.parts
    try:
        idx = parts.index("rlm")
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return "rlm"
    sub = parts[idx + 1]
    return f"rlm.{sub}" if sub in ("core", "gateway", "server", "daemon", "cli") else None


def _is_type_checking_block(node: ast.AST) -> bool:
    """Detecta ``if TYPE_CHECKING:`` e ``if typing.TYPE_CHECKING:``."""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _type_checking_lines(tree: ast.Module) -> set[int]:
    """Retorna o conjunto de linhas dentro de blocos ``if TYPE_CHECKING:``."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if _is_type_checking_block(node):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    lines.add(child.lineno)
    return lines


def _extract_imports(filepath: Path) -> list[tuple[int, str]]:
    """Extrai imports, ignorando blocos ``if TYPE_CHECKING:`` e ``# noqa: layer``."""
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    tc_lines = _type_checking_lines(tree)
    source_lines = source.splitlines()

    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if node.lineno in tc_lines:
                continue
            if node.lineno <= len(source_lines) and "# noqa: layer" in source_lines[node.lineno - 1]:
                continue
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.lineno in tc_lines:
                continue
            if node.lineno <= len(source_lines) and "# noqa: layer" in source_lines[node.lineno - 1]:
                continue
            imports.append((node.lineno, node.module))
    return imports


def check(root: Path) -> list[str]:
    """Verifica todas as violações de camada sob root/rlm/."""
    violations: list[str] = []
    rlm_root = root / "rlm"
    if not rlm_root.exists():
        return ["rlm/ directory not found"]

    for py_file in sorted(rlm_root.rglob("*.py")):
        layer = _layer_of(py_file)
        if layer is None or layer not in FORBIDDEN:
            continue

        forbidden = FORBIDDEN[layer]
        for lineno, module in _extract_imports(py_file):
            # I10: código canônico rlm/ não pode importar de packages/
            if module == "packages" or module.startswith("packages."):
                rel = py_file.relative_to(root)
                violations.append(
                    f"  {rel}:{lineno}  {layer} → {module}  (forbidden: packages/ is legacy)"
                )
                continue
            for banned in forbidden:
                if module == banned or module.startswith(banned + "."):
                    rel = py_file.relative_to(root)
                    violations.append(
                        f"  {rel}:{lineno}  {layer} → {module}  (forbidden: {banned})"
                    )
    return violations


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    violations = check(root)
    if violations:
        print(f"❌ {len(violations)} layer violation(s) found:\n")
        for v in violations:
            print(v)
        return 1
    print("✅ No layer violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
