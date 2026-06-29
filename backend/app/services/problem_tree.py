import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, insert, select, update

from app.db.database import engine, files, problems, projects


MODULE_WEIGHTS = {
    "model": 0.4,
    "backbone": 0.4,
    "training": 0.25,
    "data": 0.15,
    "utils": 0.1,
}
DEFAULT_MODULE_WEIGHT = 0.1


class PracticeModule(BaseModel):
    id: str
    title: str
    description: str = ""
    weight: float
    problem_count: int = 0
    passed_count: int = 0
    progress: float = 0.0


class PracticeCandidate(BaseModel):
    source_path: str
    symbol: str
    difficulty: str = "medium"
    problem_type: str = "function_blank"
    role_in_project: str | None = None
    depends_on: list[str] = []
    used_by: list[str] = []
    status: str
    depth: int
    generated: bool = False
    problem_id: str | None = None
    parent_id: str | None = None


class PracticePrepareResponse(BaseModel):
    modules: list[PracticeModule]
    candidates: list[PracticeCandidate]


def prepare_practice(project_id: str) -> PracticePrepareResponse:
    create_module_groups(project_id)
    project = _get_project(project_id)
    dependency_graph = _loads(project["dependency_graph"])
    existing = _existing_problem_map(project_id)
    candidates: list[PracticeCandidate] = []

    with engine.begin() as connection:
        rows = connection.execute(
            select(files.c.source_path, files.c.recommended_symbols).where(
                files.c.project_id == project_id,
                files.c.suitable == 1,
            )
        ).mappings().all()

    for row in rows:
        source_path = row["source_path"]
        for symbol in _loads_list_of_dicts(row["recommended_symbols"]):
            target_symbol = str(symbol.get("symbol") or "").strip()
            if not target_symbol:
                continue
            existing_problem = existing.get((source_path, target_symbol))
            depth = _symbol_depth(dependency_graph, source_path, target_symbol)
            depends_on = [str(item) for item in symbol.get("depends_on", [])] if isinstance(symbol.get("depends_on"), list) else []
            generated = existing_problem is not None
            symbol_status = str(symbol.get("status") or "").strip()
            status = (
                existing_problem["status"]
                if existing_problem is not None
                else symbol_status
                if symbol_status in {"skipped", "error"}
                else _candidate_status(project_id, depth, depends_on)
            )
            candidates.append(
                PracticeCandidate(
                    source_path=source_path,
                    symbol=target_symbol,
                    difficulty=str(symbol.get("difficulty") or "medium"),
                    problem_type=str(symbol.get("problem_type") or "function_blank"),
                    role_in_project=str(symbol.get("role_in_project") or "").strip() or None,
                    depends_on=depends_on,
                    used_by=[str(item) for item in symbol.get("used_by", [])] if isinstance(symbol.get("used_by"), list) else [],
                    status=status,
                    depth=depth,
                    generated=generated,
                    problem_id=existing_problem["id"] if existing_problem else None,
                    parent_id=(module_group_for_file(project_id, source_path) or {}).get("id"),
                )
            )

    return PracticePrepareResponse(
        modules=_prepare_modules(project_id, candidates),
        candidates=sorted(candidates, key=lambda item: (item.depth, item.source_path, item.symbol)),
    )


def create_module_groups(project_id: str) -> list[dict]:
    project = _get_project(project_id)
    architecture = _loads(project["architecture"])
    modules = architecture.get("modules")
    if not isinstance(modules, dict):
        return []

    existing = _module_groups_by_name(project_id)
    created_or_existing: list[dict] = []
    now = _utc_now()

    with engine.begin() as connection:
        for module_name, module in modules.items():
            if str(module_name).lower() == "config":
                continue
            if module_name in existing:
                created_or_existing.append(existing[module_name])
                continue

            if not isinstance(module, dict):
                continue
            description = str(module.get("description") or module_name)
            group_id = str(uuid.uuid4())
            connection.execute(
                insert(problems).values(
                    id=group_id,
                    project_id=project_id,
                    file_id="",
                    source_path="",
                    target_symbol=str(module_name),
                    problem_type="module_group",
                    prompt=description,
                    starter_code="",
                    test_path=None,
                    grading_method="llm",
                    original_code=None,
                    difficulty=None,
                    context=None,
                    parent_id=None,
                    weight=_module_weight(str(module_name)),
                    depth=0,
                    unlock_dependencies=json.dumps([], ensure_ascii=False),
                    role_in_project=description,
                    status="unlocked",
                    created_at=now,
                    updated_at=now,
                )
            )
            created_or_existing.append(
                {
                    "id": group_id,
                    "target_symbol": str(module_name),
                    "prompt": description,
                    "weight": _module_weight(str(module_name)),
                }
            )

    return created_or_existing


def module_group_for_file(project_id: str, source_path: str) -> dict | None:
    create_module_groups(project_id)
    project = _get_project(project_id)
    architecture = _loads(project["architecture"])
    module_name = _module_name_for_file(architecture, source_path)
    if module_name is None:
        return None
    return _module_groups_by_name(project_id).get(module_name)


def problem_tree_metadata(project_id: str, source_path: str, target_symbol: str, target_metadata=None) -> dict[str, Any]:
    project = _get_project(project_id)
    dependency_graph = _loads(project["dependency_graph"])
    dependencies = _target_dependencies(target_metadata)
    unlock_dependencies = _dependency_problem_ids(project_id, dependencies)
    module_group = module_group_for_file(project_id, source_path)
    return {
        "parent_id": module_group["id"] if module_group else None,
        "weight": 1.0,
        "depth": _symbol_depth(dependency_graph, source_path, target_symbol),
        "unlock_dependencies": unlock_dependencies,
        "status": "locked" if unlock_dependencies else "unlocked",
    }


def unlock_dependents(project_id: str, passed_problem_id: str) -> None:
    with engine.begin() as connection:
        rows = connection.execute(
            select(problems.c.id, problems.c.unlock_dependencies, problems.c.status).where(
                problems.c.project_id == project_id,
                problems.c.problem_type != "module_group",
            )
        ).mappings().all()
        statuses = {
            row["id"]: row["status"]
            for row in connection.execute(
                select(problems.c.id, problems.c.status).where(problems.c.project_id == project_id)
            ).mappings()
        }

        for row in rows:
            dependency_ids = _loads_list(row["unlock_dependencies"])
            if passed_problem_id not in dependency_ids or row["status"] != "locked":
                continue
            if all(statuses.get(dependency_id) == "passed" for dependency_id in dependency_ids):
                connection.execute(
                    update(problems)
                    .where(problems.c.id == row["id"])
                    .values(status="unlocked", updated_at=_utc_now())
                )


def module_summaries(project_id: str) -> list[dict]:
    create_module_groups(project_id)
    with engine.begin() as connection:
        groups = connection.execute(
            select(problems).where(problems.c.project_id == project_id, problems.c.problem_type == "module_group")
        ).mappings().all()
        children = connection.execute(
            select(problems.c.parent_id, problems.c.status).where(
                problems.c.project_id == project_id,
                problems.c.problem_type != "module_group",
            )
        ).mappings().all()

    summaries: list[dict] = []
    for group in groups:
        group_children = [child for child in children if child["parent_id"] == group["id"]]
        problem_count = len(group_children)
        passed_count = sum(1 for child in group_children if child["status"] == "passed")
        progress = passed_count / problem_count if problem_count else 0.0
        summaries.append(
            {
                "id": group["id"],
                "title": group["target_symbol"],
                "description": group["prompt"],
                "weight": float(group["weight"] or 1.0),
                "problem_count": problem_count,
                "passed_count": passed_count,
                "progress": progress,
            }
        )
    return sorted(summaries, key=lambda item: item["title"])


def overall_progress(project_id: str) -> float:
    modules = module_summaries(project_id)
    weighted_modules = [module for module in modules if module["problem_count"] > 0]
    if weighted_modules:
        total_weight = sum(module["weight"] for module in weighted_modules)
        if total_weight > 0:
            return sum(module["progress"] * module["weight"] for module in weighted_modules) / total_weight

    with engine.begin() as connection:
        total = connection.execute(
            select(func.count()).select_from(problems).where(
                problems.c.project_id == project_id,
                problems.c.problem_type != "module_group",
            )
        ).scalar_one()
        passed = connection.execute(
            select(func.count()).select_from(problems).where(
                problems.c.project_id == project_id,
                problems.c.problem_type != "module_group",
                problems.c.status == "passed",
            )
        ).scalar_one()
    return passed / total if total else 0.0


def _prepare_modules(project_id: str, candidates: list[PracticeCandidate]) -> list[PracticeModule]:
    group_rows = _module_groups_by_name(project_id).values()
    modules: list[PracticeModule] = []
    for group in group_rows:
        group_candidates = [candidate for candidate in candidates if candidate.parent_id == group["id"]]
        problem_count = len(group_candidates)
        passed_count = sum(1 for candidate in group_candidates if candidate.status == "passed")
        progress = passed_count / problem_count if problem_count else 0.0
        modules.append(
            PracticeModule(
                id=group["id"],
                title=group["target_symbol"],
                description=group["prompt"],
                weight=float(group["weight"] or 1.0),
                problem_count=problem_count,
                passed_count=passed_count,
                progress=progress,
            )
        )
    return sorted(modules, key=lambda item: item.title)


def _module_groups_by_name(project_id: str) -> dict[str, dict]:
    with engine.begin() as connection:
        rows = connection.execute(
            select(problems).where(problems.c.project_id == project_id, problems.c.problem_type == "module_group")
        ).mappings().all()
    return {row["target_symbol"]: dict(row) for row in rows}


def _module_name_for_file(architecture: dict[str, Any], source_path: str) -> str | None:
    modules = architecture.get("modules")
    if not isinstance(modules, dict):
        return None
    for module_name, module in modules.items():
        if str(module_name).lower() == "config" or not isinstance(module, dict):
            continue
        module_files = module.get("files")
        if isinstance(module_files, list) and source_path in module_files:
            return str(module_name)
    return None


def _symbol_depth(dependency_graph: dict[str, Any], source_path: str, target_symbol: str) -> int:
    order = dependency_graph.get("implementation_order")
    if not isinstance(order, list):
        return 0
    for item in order:
        if not isinstance(item, dict) or item.get("file") != source_path:
            continue
        symbol = str(item.get("symbol") or "")
        if symbol == target_symbol or symbol.endswith(f".{target_symbol}") or target_symbol.endswith(symbol):
            try:
                return int(item.get("depth") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _target_dependencies(target_metadata) -> list[str]:
    if target_metadata is None:
        return []
    depends_on = getattr(target_metadata, "depends_on", [])
    return [str(item) for item in depends_on] if isinstance(depends_on, list) else []


def _dependency_problem_ids(project_id: str, dependency_symbols: list[str]) -> list[str]:
    if not dependency_symbols:
        return []
    with engine.begin() as connection:
        rows = connection.execute(
            select(problems.c.id, problems.c.target_symbol).where(
                problems.c.project_id == project_id,
                problems.c.problem_type != "module_group",
            )
        ).mappings().all()
    ids: list[str] = []
    for dependency in dependency_symbols:
        for row in rows:
            target_symbol = row["target_symbol"]
            if target_symbol == dependency or target_symbol.endswith(f".{dependency}") or dependency.endswith(target_symbol):
                ids.append(row["id"])
                break
    return ids


def _candidate_status(project_id: str, depth: int, depends_on: list[str]) -> str:
    if depth == 0 or not depends_on:
        return "unlocked"
    recommended_statuses = _recommended_symbol_statuses(project_id)
    with engine.begin() as connection:
        rows = connection.execute(
            select(problems.c.target_symbol, problems.c.status).where(
                problems.c.project_id == project_id,
                problems.c.problem_type != "module_group",
            )
        ).mappings().all()
    if not rows:
        return "unlocked" if all(_dependency_is_skipped(dependency, recommended_statuses) for dependency in depends_on) else "locked"
    for dependency in depends_on:
        if _dependency_is_skipped(dependency, recommended_statuses):
            continue
        matched = [
            row
            for row in rows
            if row["target_symbol"] == dependency
            or row["target_symbol"].endswith(f".{dependency}")
            or dependency.endswith(row["target_symbol"])
        ]
        if not matched or not any(row["status"] == "passed" for row in matched):
            return "locked"
    return "unlocked"


def _dependency_is_skipped(dependency: str, statuses: dict[str, str]) -> bool:
    for symbol, status in statuses.items():
        if status not in {"skipped", "error"}:
            continue
        if symbol == dependency or symbol.endswith(f".{dependency}") or dependency.endswith(symbol):
            return True
    return False


def _recommended_symbol_statuses(project_id: str) -> dict[str, str]:
    with engine.begin() as connection:
        rows = connection.execute(
            select(files.c.recommended_symbols).where(files.c.project_id == project_id, files.c.recommended_symbols.is_not(None))
        ).mappings().all()
    statuses: dict[str, str] = {}
    for row in rows:
        for item in _loads_list_of_dicts(row["recommended_symbols"]):
            symbol = str(item.get("symbol") or "").strip()
            status = str(item.get("status") or "").strip()
            if symbol and status:
                statuses[symbol] = status
    return statuses


def _existing_problem_map(project_id: str) -> dict[tuple[str, str], dict]:
    with engine.begin() as connection:
        rows = connection.execute(
            select(problems).where(
                problems.c.project_id == project_id,
                problems.c.problem_type != "module_group",
            )
        ).mappings().all()
    return {(row["source_path"], row["target_symbol"]): dict(row) for row in rows}


def _module_weight(module_name: str) -> float:
    return MODULE_WEIGHTS.get(module_name.lower(), DEFAULT_MODULE_WEIGHT)


def _get_project(project_id: str):
    with engine.begin() as connection:
        return connection.execute(select(projects).where(projects.c.id == project_id)).mappings().first()


def _loads(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _loads_list(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _loads_list_of_dicts(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
