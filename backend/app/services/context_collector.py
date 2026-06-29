import ast
import os
from pathlib import Path


EXCLUDED_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache"}


def collect_context(repo_path: str, source_path: str, target_symbol: str) -> dict:
    repo_root = Path(repo_path)
    source_file = repo_root / source_path
    source = _read_text(source_file)

    return {
        "imports": _collect_imports(source),
        "class_init": _collect_class_init(source, target_symbol),
        "callers": _collect_callers(repo_root, target_symbol),
        "readme_snippet": _read_readme_snippet(repo_root),
    }


def _collect_imports(source: str) -> str:
    imports: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("import ") or stripped.startswith("from "):
            imports.append(line)
            continue
        break
    return "\n".join(imports)


def _collect_class_init(source: str, target_symbol: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        has_target = any(isinstance(child, ast.FunctionDef) and child.name == target_symbol for child in node.body)
        if not has_target:
            continue
        for child in node.body:
            if isinstance(child, ast.FunctionDef) and child.name == "__init__":
                return ast.get_source_segment(source, child) or ""
    return ""


def _collect_callers(repo_root: Path, target_symbol: str) -> list[str]:
    callers: list[str] = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [directory for directory in dirs if directory not in EXCLUDED_DIRS]
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = Path(root) / filename
            content = _read_text(path)
            if target_symbol in content:
                callers.append(path.relative_to(repo_root).as_posix())
    return sorted(callers)


def _read_readme_snippet(repo_root: Path) -> str:
    for name in ("README.md", "readme.md", "README.rst", "README.txt"):
        path = repo_root / name
        if path.exists():
            return _read_text(path)[:500]
    return ""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
