import ast
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update

from app.core.errors import AppError
from app.db.database import engine, projects
from app.services import llm_client
from app.services.repo_analyzer import EXCLUDED_NAMES


PROJECT_UNDERSTANDING_SYSTEM = (
    "너는 AI 연구 논문의 코드 repository를 분석하는 전문가이다.\n"
    "주어진 repository 정보를 보고 프로젝트의 전체 구조와 목적을 파악하라.\n"
    "반드시 JSON 형식으로만 응답하라."
)

MODULE_DESCRIPTIONS = {
    "model": "모델 정의",
    "data": "데이터 로딩 및 전처리",
    "training": "학습 루프",
    "config": "설정 (문제 대상 아님)",
    "utils": "유틸리티",
    "test": "테스트 (문제 대상 아님)",
    "evaluation": "평가/추론",
    "other": "기타",
}

FIRST_DIR_MAP = {
    "models": "model",
    "model": "model",
    "nets": "model",
    "networks": "model",
    "modules": "model",
    "architectures": "model",
    "data": "data",
    "datasets": "data",
    "dataloader": "data",
    "configs": "config",
    "config": "config",
    "cfg": "config",
    "train": "training",
    "training": "training",
    "engine": "training",
    "solver": "training",
    "utils": "utils",
    "tools": "utils",
    "helpers": "utils",
    "lib": "utils",
    "tests": "test",
    "test": "test",
    "demo": "evaluation",
    "demos": "evaluation",
    "eval": "evaluation",
    "evaluate": "evaluation",
    "inference": "evaluation",
    "docs": "other",
    "doc": "other",
    "scripts": "other",
    "examples": "other",
}
PROBLEM_MODULES = {"model", "data", "training"}
EXCLUDE_FROM_ORDER = {
    "__init__",
    "__new__",
    "__del__",
    "__repr__",
    "__str__",
    "register",
    "build",
    "hook",
    "hook_type",
}
EXCLUDE_ORDER_FILE_MARKERS = {"builder", "registry", "hook"}


class ProjectAnalyzeRequest(BaseModel):
    force: bool = False


class ProjectAnalyzeResponse(BaseModel):
    status: str
    project_summary: dict[str, Any]
    architecture: dict[str, Any]
    dependency_graph: dict[str, Any]


def analyze_project(project_id: str, payload: ProjectAnalyzeRequest) -> ProjectAnalyzeResponse:
    project = _get_project_row(project_id)
    if project["analysis_status"] == "completed" and not payload.force:
        return ProjectAnalyzeResponse(
            status="completed",
            project_summary=_loads(project["project_summary"]),
            architecture=_loads(project["architecture"]),
            dependency_graph=_loads(project["dependency_graph"]),
        )

    repo_path = Path(project["repo_path"])
    _update_project_analysis(project_id, analysis_status="analyzing")

    project_summary = _run_project_understanding(repo_path)
    _update_project_analysis(project_id, project_summary=json.dumps(project_summary, ensure_ascii=False))

    architecture = _run_architecture_recovery(repo_path, project_summary)
    _update_project_analysis(project_id, architecture=json.dumps(architecture, ensure_ascii=False))

    py_files = _iter_python_files(repo_path)
    non_problem_files = set(architecture.get("non_problem_files", []))
    graph_files = [path for path in py_files if path not in non_problem_files]
    dependency_graph = build_dependency_graph(repo_path, graph_files)
    _update_project_analysis(
        project_id,
        dependency_graph=json.dumps(dependency_graph, ensure_ascii=False),
        analysis_status="completed",
    )

    return ProjectAnalyzeResponse(
        status="completed",
        project_summary=project_summary,
        architecture=architecture,
        dependency_graph=dependency_graph,
    )


def build_dependency_graph(repo_path: str | Path, py_files: list[str]) -> dict[str, Any]:
    root = Path(repo_path)
    symbol_nodes: dict[str, dict[str, Any]] = {}
    callable_scopes: dict[str, set[str]] = {}
    name_index: dict[str, set[str]] = defaultdict(set)

    for source_path in py_files:
        tree = _parse_python(root / source_path)
        if tree is None:
            continue

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_key = f"{source_path}::{node.name}"
                methods = [item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
                symbol_nodes[class_key] = {"type": "class", "methods": methods}
                name_index[node.name].add(class_key)
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_key = f"{source_path}::{node.name}.{item.name}"
                        symbol_nodes[method_key] = {"type": "method", "class": node.name}
                        callable_scopes[method_key] = _collect_call_names(item)
                        name_index[item.name].add(method_key)
                        name_index[f"{node.name}.{item.name}"].add(method_key)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_key = f"{source_path}::{node.name}"
                symbol_nodes[function_key] = {"type": "function"}
                callable_scopes[function_key] = _collect_call_names(node)
                name_index[node.name].add(function_key)

    dependencies: dict[str, list[str]] = {}
    for symbol, calls in callable_scopes.items():
        resolved: set[str] = set()
        for call_name in calls:
            resolved.update(name_index.get(call_name, set()))
            if "." in call_name:
                resolved.update(name_index.get(call_name.split(".")[-1], set()))
        resolved.discard(symbol)
        dependencies[symbol] = sorted(resolved)

    depths = _calculate_depths(dependencies)
    raw_implementation_order = [
        {
            "symbol": symbol.split("::", 1)[1],
            "file": symbol.split("::", 1)[0],
            "depth": depths.get(symbol, 0),
        }
        for symbol in sorted(callable_scopes, key=lambda item: (depths.get(item, 0), item.lower()))
    ]
    implementation_order = _filter_implementation_order(raw_implementation_order)

    return {
        "symbols": symbol_nodes,
        "dependencies": dependencies,
        "implementation_order": implementation_order,
    }


def _run_project_understanding(repo_path: Path) -> dict[str, Any]:
    prompt = (
        "아래 GitHub repository를 분석하여 프로젝트 개요를 작성해주세요.\n\n"
        f"README:\n{_read_readme(repo_path)}\n\n"
        f"디렉토리 구조:\n{_directory_tree(repo_path, max_depth=2)}\n\n"
        f"주요 파일 헤더:\n{_file_headers(repo_path)}\n\n"
        f"의존성:\n{_read_requirements(repo_path)}\n\n"
        "아래 JSON 형식으로 응답하세요:\n"
        "{\n"
        '  "project_summary": "프로젝트가 무엇인지 2~3문장 설명",\n'
        '  "domain": "computer_vision | nlp | speech | reinforcement_learning | generative | other",\n'
        '  "framework": "pytorch | tensorflow | jax | other",\n'
        '  "key_components": ["backbone", "head", "loss", "training", "data"],\n'
        '  "datasets": ["COCO", "ImageNet"],\n'
        '  "main_contribution": "이 논문/프로젝트의 핵심 기여 한 줄"\n'
        "}"
    )
    parsed = llm_client.call_gemini_with_validation(
        prompt,
        PROJECT_UNDERSTANDING_SYSTEM,
        {
            "project_summary": str,
            "domain": str,
            "framework": str,
            "key_components": list,
            "datasets": list,
            "main_contribution": str,
        },
        max_retries=1,
    )
    return parsed if isinstance(parsed, dict) else {}


def _run_architecture_recovery(repo_path: Path, project_summary: dict[str, Any]) -> dict[str, Any]:
    py_files = _iter_python_files(repo_path)
    modules = classify_modules(py_files)
    file_dependencies = extract_file_dependencies(repo_path, py_files)
    non_problem_files = sorted(
        {
            path
            for module_name, module in modules.items()
            if module_name in {"config", "test"}
            for path in module["files"]
        }
        | {path for path in py_files if Path(path).name in {"setup.py", "conftest.py", "__main__.py"}}
        | {path for path in py_files if Path(path).name.startswith("__init__")}
    )
    return {
        "modules": modules,
        "file_dependencies": file_dependencies,
        "non_problem_files": non_problem_files,
    }


def classify_modules(py_files: list[str]) -> dict[str, dict[str, Any]]:
    modules: dict[str, dict[str, Any]] = {}
    for path in py_files:
        module_name = _classify_module(path)
        modules.setdefault(module_name, {"description": MODULE_DESCRIPTIONS[module_name], "files": []})
        modules[module_name]["files"].append(path)
    return modules


def extract_file_dependencies(repo_path: str | Path, py_files: list[str]) -> dict[str, list[str]]:
    root = Path(repo_path)
    module_index = _module_to_file_index(py_files)
    dependencies: dict[str, list[str]] = {}
    for source_path in py_files:
        imports = _extract_import_modules(root / source_path)
        resolved = set()
        for import_name in imports:
            resolved.update(_resolve_import_to_files(import_name, module_index))
        resolved.discard(source_path)
        dependencies[source_path] = sorted(resolved)
    return dependencies


def _read_readme(repo_path: Path) -> str:
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        path = repo_path / name
        if path.exists():
            return _read_text(path)
    return ""


def _directory_tree(repo_path: Path, max_depth: int) -> str:
    lines: list[str] = []
    for path in sorted(repo_path.rglob("*"), key=lambda item: item.relative_to(repo_path).as_posix().lower()):
        relative = path.relative_to(repo_path)
        if len(relative.parts) > max_depth or _is_excluded(path, repo_path):
            continue
        indent = "  " * (len(relative.parts) - 1)
        suffix = "/" if path.is_dir() else ""
        lines.append(f"{indent}{relative.name}{suffix}")
    return "\n".join(lines)


def _file_headers(repo_path: Path) -> str:
    chunks: list[str] = []
    for source_path in _iter_python_files(repo_path)[:10]:
        path = repo_path / source_path
        header = "\n".join(_read_text(path).splitlines()[:30])
        chunks.append(f"===== {source_path} =====\n{header}")
    return "\n\n".join(chunks)


def _read_requirements(repo_path: Path) -> str:
    chunks = []
    for name in ("requirements.txt", "pyproject.toml"):
        path = repo_path / name
        if path.exists():
            chunks.append(f"===== {name} =====\n{_read_text(path)}")
    return "\n\n".join(chunks)


def _collect_file_imports(repo_path: Path) -> dict[str, list[str]]:
    imports: dict[str, list[str]] = {}
    for source_path in _iter_python_files(repo_path):
        imports[source_path] = _extract_imports(repo_path / source_path)
    return imports


def _extract_imports(path: Path) -> list[str]:
    tree = _parse_python(path)
    if tree is None:
        return [
            line.strip()
            for line in _read_text(path).splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            segment = ast.get_source_segment(_read_text(path), node)
            if segment:
                imports.append(segment.strip())
    return imports


def _extract_import_modules(path: Path) -> list[str]:
    tree = _parse_python(path)
    if tree is None:
        modules = []
        for line in _read_text(path).splitlines():
            stripped = line.strip()
            if stripped.startswith("import "):
                modules.extend(item.strip().split(" as ")[0] for item in stripped.removeprefix("import ").split(","))
            elif stripped.startswith("from "):
                modules.append(stripped.split()[1].lstrip("."))
        return [module for module in modules if module]

    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _classify_module(path: str) -> str:
    parts = [part.lower() for part in Path(path).parts]
    if len(parts) > 1:
        first_dir = parts[0]
        if first_dir in FIRST_DIR_MAP:
            return FIRST_DIR_MAP[first_dir]
        for part in parts[:-1]:
            if part in FIRST_DIR_MAP:
                return FIRST_DIR_MAP[part]
        return "other"

    return _classify_by_filename(path)


def _filter_implementation_order(order: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for entry in order:
        file_path = str(entry.get("file") or "")
        symbol = str(entry.get("symbol") or "")
        leaf_symbol = symbol.split(".")[-1]
        file_name = Path(file_path).name.lower()

        if _classify_module(file_path) not in PROBLEM_MODULES:
            continue
        if file_name == "__init__.py" or "__init__.py" in file_path.replace("\\", "/"):
            continue
        if leaf_symbol in EXCLUDE_FROM_ORDER:
            continue
        if any(marker in file_name for marker in EXCLUDE_ORDER_FILE_MARKERS):
            continue
        filtered.append(entry)
    return filtered


def _classify_by_filename(path: str) -> str:
    name = Path(path).stem.lower()
    if "train" in name:
        return "training"
    if "config" in name or "cfg" in name:
        return "config"
    if "test" in name:
        return "test"
    if "eval" in name or "infer" in name:
        return "evaluation"
    return "other"


def _module_to_file_index(py_files: list[str]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for source_path in py_files:
        without_suffix = source_path.removesuffix(".py")
        dotted = without_suffix.replace("/", ".").replace("\\", ".")
        parts = dotted.split(".")
        for start in range(len(parts)):
            index[".".join(parts[start:])].add(source_path)
        if Path(source_path).name == "__init__.py":
            package = ".".join(parts[:-1])
            if package:
                index[package].add(source_path)
        index[Path(source_path).stem].add(source_path)
    return index


def _resolve_import_to_files(import_name: str, module_index: dict[str, set[str]]) -> set[str]:
    candidates: set[str] = set()
    parts = import_name.split(".")
    for end in range(len(parts), 0, -1):
        key = ".".join(parts[:end])
        candidates.update(module_index.get(key, set()))
    candidates.update(module_index.get(parts[-1], set()))
    return candidates


def _iter_python_files(repo_path: Path) -> list[str]:
    return [
        path.relative_to(repo_path).as_posix()
        for path in sorted(repo_path.rglob("*.py"), key=lambda item: item.relative_to(repo_path).as_posix().lower())
        if path.is_file() and not _is_excluded(path, repo_path)
    ]


def _parse_python(path: Path) -> ast.Module | None:
    try:
        return ast.parse(_read_text(path), filename=str(path))
    except SyntaxError:
        return None


def _collect_call_names(node: ast.AST) -> set[str]:
    calls: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child.func)
        if name:
            calls.add(name)
    return calls


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _calculate_depths(dependencies: dict[str, list[str]]) -> dict[str, int]:
    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(symbol: str) -> int:
        if symbol in depths:
            return depths[symbol]
        if symbol in visiting:
            return 0
        visiting.add(symbol)
        internal_dependencies = [item for item in dependencies.get(symbol, []) if item in dependencies]
        value = 0 if not internal_dependencies else 1 + max(depth(item) for item in internal_dependencies)
        visiting.remove(symbol)
        depths[symbol] = value
        return value

    for symbol in dependencies:
        depth(symbol)
    return depths


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _is_excluded(path: Path, root: Path) -> bool:
    return any(part in EXCLUDED_NAMES for part in path.relative_to(root).parts)


def _get_project_row(project_id: str):
    with engine.begin() as connection:
        row = connection.execute(select(projects).where(projects.c.id == project_id)).mappings().first()
    if row is None:
        raise AppError("PROJECT_NOT_FOUND", "Project not found.")
    return row


def _update_project_analysis(project_id: str, **values: Any) -> None:
    values["updated_at"] = _utc_now()
    with engine.begin() as connection:
        connection.execute(update(projects).where(projects.c.id == project_id).values(**values))


def _loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
