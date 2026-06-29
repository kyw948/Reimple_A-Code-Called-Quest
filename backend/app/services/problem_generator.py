import ast
import builtins
import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import insert, select, update

from app.core.errors import AppError
from app.db.database import engine, files, problems
from app.services import llm_client
from app.services.context_collector import collect_context
from app.services.project_service import _get_project_row, _utc_now
from app.services.problem_tree import create_module_groups, module_summaries, overall_progress, problem_tree_metadata
from app.services.test_matcher import find_test_file


PROBLEM_PROMPT_SYSTEM_INSTRUCTION = (
    "너는 코딩 연습 문제 출제자이다.\n"
    "주어진 함수에 대해 학습자가 구현할 수 있도록 풍부한 문제 설명을 작성하라.\n"
    "마크다운 형식으로 작성하라.\n"
    "원본 구현 코드는 절대 포함하지 마라."
)
PARTIAL_STARTER_SYSTEM_INSTRUCTION = (
    "너는 코딩 연습 문제 출제자이다.\n"
    "주어진 함수에서 핵심 로직 부분만 빈칸으로 만들어라.\n"
    "나머지 코드(변수 선언, 입력 처리, 반환문 등)는 그대로 유지하라."
)
NON_PYTHON_PROBLEM_SYSTEM_INSTRUCTION = (
    "너는 코딩 연습 문제 출제자이다.\n"
    "Python이 아닌 소스 파일에서 지정된 함수나 메서드를 찾아 연습 문제를 만들어라.\n"
    "반드시 JSON 형식으로만 응답하라."
)
PROBLEM_ASSETS_SYSTEM_INSTRUCTION = (
    "너는 AI 논문 코드 기반 코딩 연습 문제 출제자이다.\n"
    "주어진 함수에 대해 문제 설명과 starter_code를 생성하라.\n"
    "반드시 JSON 형식으로만 응답하라."
)

ProblemType = Literal["function_blank", "function_partial"]

ALLOWED_DECORATORS = {"staticmethod", "classmethod"}
DANGEROUS_CALL_NAMES = {"open"}
DANGEROUS_CALL_ROOTS = {"subprocess", "socket", "requests"}
DANGEROUS_CALL_ATTRIBUTES = {
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("shutil", "rmtree"),
}
IGNORED_EXTERNAL_NAMES = set(dir(builtins)) | {"self", "cls", "True", "False", "None"}


class TargetSymbol(BaseModel):
    symbol: str
    difficulty: Literal["easy", "medium", "hard"] | None = None
    problem_type: ProblemType = "function_blank"
    role_in_project: str | None = None
    depends_on: list[str] = []
    used_by: list[str] = []


class ProblemGenerateRequest(BaseModel):
    source_path: str
    target_symbols: list[TargetSymbol] | None = None


class ProblemGenerateItem(BaseModel):
    problem_id: str
    file_id: str
    source_path: str
    target_symbol: str
    problem_type: ProblemType
    test_path: str | None
    grading_method: Literal["pytest", "llm"]
    difficulty: Literal["easy", "medium", "hard"] | None = None


class ProblemGenerateResponse(BaseModel):
    problems: list[ProblemGenerateItem]
    problem_id: str | None = None
    file_id: str | None = None
    source_path: str | None = None
    target_symbol: str | None = None
    problem_type: ProblemType | None = None
    test_path: str | None = None
    grading_method: Literal["pytest", "llm"] | None = None
    difficulty: Literal["easy", "medium", "hard"] | None = None


class ProblemListItem(BaseModel):
    id: str
    source_path: str
    target_symbol: str
    status: str
    grading_method: Literal["pytest", "llm"]
    difficulty: Literal["easy", "medium", "hard"] | None = None
    problem_type: ProblemType = "function_blank"
    parent_id: str | None = None
    depth: int = 0
    unlock_dependencies: list[str] = []
    role_in_project: str | None = None


class ModuleSummary(BaseModel):
    id: str
    title: str
    description: str
    weight: float
    problem_count: int
    passed_count: int
    progress: float


class ProblemListResponse(BaseModel):
    problems: list[ProblemListItem]
    modules: list[ModuleSummary] = []
    overall_progress: float = 0.0


class ProblemDetail(BaseModel):
    id: str
    project_id: str
    file_id: str
    source_path: str
    target_symbol: str
    problem_type: ProblemType
    prompt: str
    starter_code: str
    test_path: str | None
    grading_method: Literal["pytest", "llm"]
    original_code: str | None = None
    difficulty: Literal["easy", "medium", "hard"] | None = None
    context: str | None = None
    parent_id: str | None = None
    weight: float = 1.0
    depth: int = 0
    unlock_dependencies: str | None = None
    role_in_project: str | None = None
    status: str
    created_at: str
    updated_at: str


@dataclass
class FunctionCandidate:
    node: ast.FunctionDef
    name: str
    score: int
    prompt: str
    starter_code: str
    original_code: str


def generate_problem(project_id: str, payload: ProblemGenerateRequest) -> ProblemGenerateResponse:
    project = _get_project_row(project_id)
    create_module_groups(project_id)
    repo_path = Path(project["repo_path"])
    source_path = payload.source_path
    source_file = _resolve_source_file(repo_path, source_path)
    source = source_file.read_text(encoding="utf-8")
    if source_file.suffix.lower() != ".py":
        file_row = _get_file_row(project_id, source_path)
        return _generate_non_python_problem(
            project_id=project_id,
            file_id=file_row["id"],
            repo_path=repo_path,
            source_path=source_path,
            extension=source_file.suffix,
            source=source,
            payload=payload,
        )

    try:
        tree = ast.parse(source)
    except SyntaxError:
        if not payload.target_symbols:
            raise AppError("TEST_NOT_FOUND", "Test file not found.")
        raise AppError("SYMBOL_NOT_FOUND", f"'{source_path}'에서 문제로 만들 함수를 찾을 수 없습니다.")
    file_row = _get_file_row(project_id, source_path)

    test_path = find_test_file(repo_path, source_path)
    test_source = ""
    grading_method: Literal["pytest", "llm"] = "llm"
    if test_path is not None:
        if not _baseline_passes(repo_path, test_path):
            raise AppError("BASELINE_TEST_FAILED", "Baseline test failed.")
        test_source = (repo_path / test_path).read_text(encoding="utf-8")
        grading_method = "pytest"

    requested_targets = _requested_targets(payload, file_row)
    candidates = (
        _candidates_for_targets(tree, source, requested_targets)
        if requested_targets
        else _best_legacy_candidate(tree, source, test_source)
    )

    created_or_existing: list[ProblemGenerateItem] = []
    for candidate, difficulty, problem_type in candidates:
        target_metadata = _target_metadata_for_candidate(candidate.name, requested_targets)
        existing = _get_existing_problem(project_id, source_path, candidate.name)
        if existing is not None:
            created_or_existing.append(_problem_generate_item(existing))
            continue

        created_or_existing.append(
            _create_problem(
                project_id=project_id,
                file_id=file_row["id"],
                repo_path=str(repo_path),
                source_path=source_path,
                candidate=candidate,
                test_path=test_path,
                grading_method=grading_method,
                difficulty=difficulty,
                requested_problem_type=problem_type,
                target_metadata=target_metadata,
            )
        )

    if not created_or_existing:
        requested = ", ".join(target.symbol for target in requested_targets) or "추천 심볼"
        available_symbols = _available_symbols(tree)
        for target in requested_targets:
            _mark_recommended_symbol_status(project_id, source_path, target.symbol, "skipped", "함수를 찾을 수 없습니다")
        print(f"[generate] Looking for '{requested}' in {source_path}")
        print(f"[generate] Available symbols: {available_symbols}")
        raise AppError("SYMBOL_NOT_FOUND", f"'{requested}' 함수를 {source_path}에서 찾을 수 없습니다.")

    now = _utc_now()
    with engine.begin() as connection:
        connection.execute(
            update(files)
            .where(files.c.id == file_row["id"])
            .values(status="problem_created", updated_at=now)
        )

    return _problem_generate_response(created_or_existing)


def list_project_problems(project_id: str) -> ProblemListResponse:
    _get_project_row(project_id)
    with engine.begin() as connection:
        rows = (
            connection.execute(
                select(
                    problems.c.id,
                    problems.c.source_path,
                    problems.c.target_symbol,
                    problems.c.problem_type,
                    problems.c.status,
                    problems.c.grading_method,
                    problems.c.difficulty,
                    problems.c.parent_id,
                    problems.c.depth,
                    problems.c.unlock_dependencies,
                    problems.c.role_in_project,
                )
                .where(problems.c.project_id == project_id, problems.c.problem_type != "module_group")
                .order_by(problems.c.source_path.asc(), problems.c.created_at.asc())
            )
            .mappings()
            .all()
        )

    return ProblemListResponse(
        problems=[_problem_list_item(row) for row in rows],
        modules=[ModuleSummary(**module) for module in module_summaries(project_id)],
        overall_progress=overall_progress(project_id),
    )


def get_problem_detail(problem_id: str) -> ProblemDetail:
    with engine.begin() as connection:
        row = connection.execute(select(problems).where(problems.c.id == problem_id)).mappings().first()

    if row is None:
        raise AppError("PROBLEM_NOT_FOUND", "Problem not found.")

    return ProblemDetail(**dict(row))


def select_problem_candidate(source_file: Path, test_source: str) -> FunctionCandidate | None:
    source = source_file.read_text(encoding="utf-8")
    tree = ast.parse(source)
    candidates = [
        _build_candidate(node, source, test_source, is_method=is_method, target_name=target_name)
        for node, is_method, target_name in _iter_supported_functions(tree)
        if _is_valid_candidate(node)
    ]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return None

    return max(candidates, key=lambda candidate: (candidate.score, -candidate.node.lineno))


def _requested_targets(payload: ProblemGenerateRequest, file_row=None) -> list[TargetSymbol]:
    targets = payload.target_symbols or []
    requested = [target for target in targets if target.symbol.strip()]
    if requested or file_row is None:
        return requested
    return _targets_from_saved_recommendations(file_row)


def _targets_from_saved_recommendations(file_row) -> list[TargetSymbol]:
    raw = file_row.get("recommended_symbols") if hasattr(file_row, "get") else file_row["recommended_symbols"]
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    targets: list[TargetSymbol] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        symbol = str(value.get("symbol") or "").strip()
        if not symbol:
            continue
        difficulty = value.get("difficulty")
        if difficulty not in {"easy", "medium", "hard"}:
            difficulty = None
        problem_type = value.get("problem_type")
        if problem_type not in {"function_blank", "function_partial"}:
            problem_type = "function_blank"
        depends_on = value.get("depends_on")
        used_by = value.get("used_by")
        targets.append(
            TargetSymbol(
                symbol=symbol,
                difficulty=difficulty,
                problem_type=problem_type,
                role_in_project=str(value.get("role_in_project") or "").strip() or None,
                depends_on=[str(item) for item in depends_on] if isinstance(depends_on, list) else [],
                used_by=[str(item) for item in used_by] if isinstance(used_by, list) else [],
            )
        )
    return targets


def _target_metadata_for_candidate(candidate_name: str, requested_targets: list[TargetSymbol]) -> TargetSymbol | None:
    for target in requested_targets:
        if target.symbol == candidate_name:
            return target
    candidate_leaf = _symbol_leaf(candidate_name)
    for target in requested_targets:
        if target.symbol == candidate_leaf:
            return target
    return None


def _generate_non_python_problem(
    project_id: str,
    file_id: str,
    repo_path: Path,
    source_path: str,
    extension: str,
    source: str,
    payload: ProblemGenerateRequest,
) -> ProblemGenerateResponse:
    file_row = _get_file_row(project_id, source_path)
    requested_targets = _requested_targets(payload, file_row)
    if not requested_targets:
        raise AppError("PROBLEM_NOT_FOUND", "target_symbol is required for non-Python files.")

    created_or_existing: list[ProblemGenerateItem] = []
    for target in requested_targets:
        existing = _get_existing_problem(project_id, source_path, target.symbol)
        if existing is not None:
            created_or_existing.append(_problem_generate_item(existing))
            continue

        try:
            generated = _generate_non_python_payload(extension, source_path, source, target.symbol)
        except AppError as exc:
            if exc.detail.get("error_code") == "SYMBOL_NOT_FOUND":
                _mark_recommended_symbol_status(project_id, source_path, target.symbol, "skipped", exc.detail["message"])
                continue
            raise
        created_or_existing.append(
            _create_non_python_problem(
                project_id=project_id,
                file_id=file_id,
                repo_path=repo_path,
                source_path=source_path,
                target=target,
                generated=generated,
            )
        )

    if not created_or_existing:
        requested = ", ".join(target.symbol for target in requested_targets) or "추천 심볼"
        for target in requested_targets:
            _mark_recommended_symbol_status(project_id, source_path, target.symbol, "skipped", "함수를 찾을 수 없습니다")
        print(f"[generate] Looking for '{requested}' in {source_path}")
        print("[generate] Available symbols: []")
        raise AppError("SYMBOL_NOT_FOUND", f"'{requested}' 함수를 {source_path}에서 찾을 수 없습니다.")

    now = _utc_now()
    with engine.begin() as connection:
        connection.execute(update(files).where(files.c.id == file_id).values(status="problem_created", updated_at=now))

    return _problem_generate_response(created_or_existing)


def _generate_non_python_payload(extension: str, source_path: str, source: str, target_symbol: str) -> dict:
    parsed = llm_client.call_gemini_with_validation(
        _build_non_python_problem_prompt(extension, source, target_symbol),
        NON_PYTHON_PROBLEM_SYSTEM_INSTRUCTION,
        {"original_code": str, "starter_code": str, "prompt": str},
        max_retries=1,
    )
    if parsed is None or not isinstance(parsed, dict):
        raise AppError("SYMBOL_NOT_FOUND", f"'{target_symbol}' 함수를 찾을 수 없습니다.")
    original_code = str(parsed.get("original_code") or "").strip()
    starter_code = str(parsed.get("starter_code") or "").strip()
    prompt = str(parsed.get("prompt") or "").strip()
    leaf_symbol = _symbol_leaf(target_symbol)

    if not original_code or leaf_symbol not in original_code:
        raise AppError("SYMBOL_NOT_FOUND", f"'{target_symbol}' 함수를 {source_path}에서 찾을 수 없습니다.")
    if not starter_code or leaf_symbol not in starter_code or "TODO" not in starter_code:
        raise AppError("SYMBOL_NOT_FOUND", f"'{target_symbol}' 함수의 starter code를 생성하지 못했습니다.")
    if not prompt:
        raise AppError("SYMBOL_NOT_FOUND", f"'{target_symbol}' 함수의 문제 설명을 생성하지 못했습니다.")

    return {
        "original_code": original_code,
        "starter_code": starter_code,
        "prompt": prompt,
    }


def _build_non_python_problem_prompt(extension: str, source: str, target_symbol: str) -> str:
    return f"""아래 소스 파일에서 {target_symbol} 함수를 찾아 코딩 연습 문제를 만들어주세요.

파일 ({extension}):
```
{source}
```

JSON으로 응답하세요:
{{
  "original_code": "해당 함수의 원본 코드 전체",
  "starter_code": "함수 body를 TODO 주석과 빈칸으로 치환한 전체 파일 코드",
  "prompt": "마크다운 형식의 문제 설명"
}}

starter_code 규칙:
- 해당 함수의 핵심 로직만 // TODO: implement this function 으로 치환
- 나머지 함수/변수/import는 원본 유지
- 파일 전체 코드를 반환 (함수만이 아님)
"""


def _parse_json_response(raw_response: str) -> dict:
    text = _strip_markdown_code_block(raw_response)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be an object.")
    return parsed


def _candidates_for_targets(
    tree: ast.Module,
    source: str,
    requested_targets: list[TargetSymbol],
) -> list[tuple[FunctionCandidate, Literal["easy", "medium", "hard"] | None, ProblemType]]:
    supported: dict[str, tuple[ast.FunctionDef, bool, str]] = {}
    leaf_index: dict[str, list[tuple[ast.FunctionDef, bool, str]]] = {}
    for node, is_method, target_name in _iter_supported_functions(tree):
        supported[target_name] = (node, is_method, target_name)
        leaf_index.setdefault(node.name, []).append((node, is_method, target_name))
    candidates: list[tuple[FunctionCandidate, Literal["easy", "medium", "hard"] | None, ProblemType]] = []
    for target in requested_targets:
        match = supported.get(target.symbol)
        if match is None and "." not in target.symbol:
            leaf_matches = leaf_index.get(target.symbol, [])
            if len(leaf_matches) == 1:
                match = leaf_matches[0]
        if match is None:
            continue
        node, is_method, target_name = match
        candidates.append(
            (_build_candidate(node, source, "", is_method=is_method, target_name=target_name), target.difficulty, target.problem_type)
        )
    return candidates


def _best_legacy_candidate(tree: ast.Module, source: str, test_source: str) -> list[tuple[FunctionCandidate, None, ProblemType]]:
    candidates = [
        _build_candidate(node, source, test_source, is_method=is_method, target_name=target_name)
        for node, is_method, target_name in _iter_supported_functions(tree)
        if _is_valid_candidate(node)
    ]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return []
    return [(max(candidates, key=lambda candidate: (candidate.score, -candidate.node.lineno)), None, "function_blank")]


def _iter_supported_functions(tree: ast.Module) -> list[tuple[ast.FunctionDef, bool, str]]:
    functions: list[tuple[ast.FunctionDef, bool, str]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions.append((node, False, node.name))
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    functions.append((child, True, f"{node.name}.{child.name}"))
    return functions


def _available_symbols(tree: ast.Module) -> list[str]:
    return [target_name for _, _, target_name in _iter_supported_functions(tree)]


def _symbol_leaf(symbol: str) -> str:
    return symbol.split(".")[-1]


def _is_valid_candidate(node: ast.FunctionDef) -> bool:
    if _is_dunder_method(node.name):
        return False
    if not _decorators_are_allowed(node):
        return False
    if not any(isinstance(child, ast.Return) for child in ast.walk(node)):
        return False
    if any(isinstance(child, (ast.Yield, ast.YieldFrom)) for child in ast.walk(node)):
        return False
    if not 1 <= _parameter_count(node.args) <= 5:
        return False
    if not 3 <= _body_line_count(node) <= 80:
        return False
    if _has_dangerous_direct_call(node):
        return False
    return True


def _build_candidate(
    node: ast.FunctionDef,
    source: str,
    test_source: str,
    is_method: bool,
    target_name: str | None = None,
) -> FunctionCandidate:
    signature = _function_signature(node)
    docstring = ast.get_docstring(node)
    prompt_body = docstring if docstring else "함수명과 파라미터를 참고하여 구현하세요."
    prompt = f"아래 함수를 구현하세요.\n{signature}\n{prompt_body}"
    starter_code = _replace_function_body(source, node)
    original_code = _extract_function_source(source, node)

    score = 0
    if _has_type_hint(node):
        score += 2
    if docstring:
        score += 1
    if node.name in test_source:
        score += 5
    if not is_method:
        score += 1
    if _has_many_external_dependencies(node):
        score -= 3

    return FunctionCandidate(
        node=node,
        name=target_name or node.name,
        score=score,
        prompt=prompt,
        starter_code=starter_code,
        original_code=original_code,
    )


def _generate_markdown_prompt(
    source_path: str,
    candidate: FunctionCandidate,
    difficulty: Literal["easy", "medium", "hard"] | None,
    context: dict,
) -> str:
    signature = _function_signature(candidate.node)
    docstring = ast.get_docstring(candidate.node) or "문서화된 설명이 없습니다."
    prompt = _build_problem_description_prompt(
        source_path=source_path,
        target_symbol=candidate.name,
        signature=signature,
        docstring=docstring,
        difficulty=difficulty,
        context=context,
    )
    try:
        markdown = _call_problem_description_llm(prompt)
    except Exception:
        return candidate.prompt

    markdown = markdown.strip()
    if not markdown:
        return candidate.prompt
    return markdown


def _generate_problem_assets(
    repo_path: str,
    source_path: str,
    candidate: FunctionCandidate,
    difficulty: Literal["easy", "medium", "hard"] | None,
    problem_type: ProblemType,
    context: dict,
) -> tuple[str, str, ProblemType]:
    generated = _generate_problem_assets_llm(repo_path, source_path, candidate, difficulty, problem_type, context)
    if generated is not None:
        return generated["prompt"], generated["starter_code"], problem_type

    prompt = _generate_markdown_prompt(source_path, candidate, difficulty, context)
    starter_code = candidate.starter_code
    final_problem_type = problem_type
    if problem_type == "function_partial":
        partial_starter = _build_partial_starter(candidate)
        if partial_starter is not None:
            starter_code = partial_starter
        else:
            final_problem_type = "function_blank"
    return prompt, starter_code, final_problem_type


def _generate_problem_assets_llm(
    repo_path: str,
    source_path: str,
    candidate: FunctionCandidate,
    difficulty: Literal["easy", "medium", "hard"] | None,
    problem_type: ProblemType,
    context: dict,
) -> dict | None:
    full_file_content = (Path(repo_path) / source_path).read_text(encoding="utf-8")
    prompt = _build_problem_assets_prompt(
        source_path=source_path,
        target_symbol=candidate.name,
        full_file_content=full_file_content,
        difficulty=difficulty,
        problem_type=problem_type,
        context=context,
    )
    try:
        parsed = _call_problem_assets_llm(prompt)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None

    markdown_prompt = str(parsed.get("prompt") or "").strip()
    starter_code = _strip_markdown_code_block(str(parsed.get("starter_code") or "")).strip()
    if not markdown_prompt or not starter_code:
        return None
    if _symbol_leaf(candidate.name) not in starter_code:
        return None
    if "TODO" not in starter_code and "NotImplementedError" not in starter_code and "pass" not in starter_code:
        return None
    return {"prompt": markdown_prompt, "starter_code": starter_code}


def _call_problem_assets_llm(prompt: str) -> dict | list | None:
    return llm_client.call_gemini_with_validation(
        prompt,
        PROBLEM_ASSETS_SYSTEM_INSTRUCTION,
        {"prompt": str, "starter_code": str},
        max_retries=1,
    )


def _build_problem_assets_prompt(
    source_path: str,
    target_symbol: str,
    full_file_content: str,
    difficulty: Literal["easy", "medium", "hard"] | None,
    problem_type: ProblemType,
    context: dict,
) -> str:
    depends_on = context.get("depends_on") or []
    used_by = context.get("used_by") or []
    return f"""아래 함수에 대한 코딩 연습 문제를 만들어주세요.

파일: {source_path}
함수: {target_symbol}
전체 파일 코드:
```python
{full_file_content}
```

프로젝트: {context.get("project_summary") or context.get("readme_snippet") or "없음"}
이 함수의 역할: {context.get("role_in_project") or "없음"}
의존 모듈: {", ".join(depends_on) if depends_on else "없음"}
사용처: {", ".join(used_by) if used_by else "없음"}
난이도: {difficulty or "미지정"}

문제 유형: {problem_type} (function_blank이면 함수 전체를 TODO로, function_partial이면 핵심 부분만 TODO로)

JSON으로 응답:
{{
  "prompt": "마크다운 형식 문제 설명. 원본 코드 포함 금지. 함수 역할, 입출력, 동작, 난이도 포함.",
  "starter_code": "해당 함수의 body를 TODO로 치환한 전체 파일 코드. 나머지 함수/클래스/import는 원본 유지."
}}

starter_code 규칙:
- function_blank: 함수 body 전체를 # TODO: implement this function\\n    raise NotImplementedError 로 치환
- function_partial: 핵심 로직만 # TODO 주석으로 치환, 나머지는 유지
- 파일 전체 코드를 반환할 것 (함수만이 아님)
- 마크다운 코드블록(```)으로 감싸지 말 것
"""


def _call_problem_description_llm(prompt: str) -> str:
    return llm_client.call_gemini(
        prompt,
        PROBLEM_PROMPT_SYSTEM_INSTRUCTION,
        response_mime_type=None,
    )


def _build_partial_starter(candidate: FunctionCandidate) -> str | None:
    prompt = f"""아래 함수에서 핵심 로직 부분만 빈칸(TODO)으로 만들어주세요.

원본 함수:
```python
{candidate.original_code}
```

규칙:
1. 함수 시그니처는 그대로 유지
2. 변수 선언, import, 입력 전처리 등은 그대로 유지
3. 핵심 알고리즘/로직 부분만 TODO 주석과 빈칸으로 교체
4. TODO 주석에 구현 방향 힌트를 한 줄 추가
5. 빈칸은 pass로 표시
6. 원본 코드의 들여쓰기를 정확히 유지

응답은 수정된 함수 코드만 반환하세요. 설명은 포함하지 마세요.
마크다운 코드 블록(```)으로 감싸지 마세요. 순수 Python 코드만 반환하세요.
"""
    try:
        starter_code = _strip_markdown_code_block(_call_partial_starter_llm(prompt)).strip()
    except Exception:
        return None
    if not _is_valid_partial_starter(starter_code, candidate.name):
        return None
    return starter_code


def _call_partial_starter_llm(prompt: str) -> str:
    return llm_client.call_gemini(
        prompt,
        PARTIAL_STARTER_SYSTEM_INSTRUCTION,
        response_mime_type=None,
    )


def _strip_markdown_code_block(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _is_valid_partial_starter(starter_code: str, target_symbol: str) -> bool:
    if not starter_code.strip():
        return False
    if _symbol_leaf(target_symbol) not in starter_code:
        return False
    return "TODO" in starter_code or "pass" in starter_code


def _build_problem_description_prompt(
    source_path: str,
    target_symbol: str,
    signature: str,
    docstring: str,
    difficulty: Literal["easy", "medium", "hard"] | None,
    context: dict,
) -> str:
    callers = context.get("callers") or []
    caller_text = ", ".join(callers) if callers else "없음"
    depends_on = context.get("depends_on") or []
    used_by = context.get("used_by") or []
    return f"""아래 함수에 대한 코딩 연습 문제 설명을 작성해주세요.

## 대상 함수
- 파일: {source_path}
- 함수명: {target_symbol}
- 시그니처: {signature}
- docstring: {docstring}

## 프로그램 맥락
- import 목록:
{context.get("imports") or "없음"}

- 클래스 구조 (__init__):
{context.get("class_init") or "없음"}

- 이 함수를 호출하는 파일: {caller_text}

- 프로젝트 설명:
{context.get("readme_snippet") or "없음"}

- 프로젝트:
{context.get("project_summary") or "없음"}

- 이 함수의 역할:
{context.get("role_in_project") or "없음"}

- 이 함수가 사용하는 모듈 (이전 문제에서 구현함):
{", ".join(depends_on) if depends_on else "없음"}

- 이 함수를 사용하는 모듈 (이 문제 이후에 구현할 예정):
{", ".join(used_by) if used_by else "없음"}

## 난이도: {difficulty or "미지정"}

아래 형식으로 마크다운 문제 설명을 작성하세요:

### {target_symbol} 함수 구현

[이 함수가 프로그램에서 하는 역할 2~3문장]

#### 입력
- 각 파라미터의 의미와 타입 설명

#### 출력
- 반환값의 의미와 타입 설명

#### 동작
- 기대하는 동작을 단계별로 설명 (구체적 코드 없이)

#### 참고
- 관련 클래스/모듈 정보
- 난이도 표시

#### 주의사항
- 엣지 케이스나 특별히 주의할 점

원본 코드는 절대 포함하지 마세요.
함수 시그니처와 동작 설명만 제공하세요.
"""


def _create_problem(
    project_id: str,
    file_id: str,
    repo_path: str,
    source_path: str,
    candidate: FunctionCandidate,
    test_path: str | None,
    grading_method: Literal["pytest", "llm"],
    difficulty: Literal["easy", "medium", "hard"] | None,
    requested_problem_type: ProblemType,
    target_metadata: TargetSymbol | None = None,
) -> ProblemGenerateItem:
    now = _utc_now()
    problem_id = str(uuid.uuid4())
    collected_context = collect_context(repo_path, source_path, candidate.name)
    collected_context.update(_project_generation_context(project_id))
    if target_metadata and target_metadata.role_in_project:
        collected_context["role_in_project"] = target_metadata.role_in_project
        collected_context["depends_on"] = target_metadata.depends_on
        collected_context["used_by"] = target_metadata.used_by
    prompt, starter_code, problem_type = _generate_problem_assets(
        repo_path=repo_path,
        source_path=source_path,
        candidate=candidate,
        difficulty=difficulty,
        problem_type=requested_problem_type,
        context=collected_context,
    )
    context_json = json.dumps(collected_context, ensure_ascii=False)
    tree_metadata = problem_tree_metadata(project_id, source_path, candidate.name, target_metadata)

    with engine.begin() as connection:
        connection.execute(
            insert(problems).values(
                id=problem_id,
                project_id=project_id,
                file_id=file_id,
                source_path=source_path,
                target_symbol=candidate.name,
                problem_type=problem_type,
                prompt=prompt,
                starter_code=starter_code,
                test_path=test_path,
                grading_method=grading_method,
                original_code=candidate.original_code if grading_method == "llm" else None,
                difficulty=difficulty,
                context=context_json,
                parent_id=tree_metadata["parent_id"],
                weight=tree_metadata["weight"],
                depth=tree_metadata["depth"],
                unlock_dependencies=json.dumps(tree_metadata["unlock_dependencies"], ensure_ascii=False),
                role_in_project=target_metadata.role_in_project if target_metadata else None,
                status=tree_metadata["status"],
                created_at=now,
                updated_at=now,
            )
        )

    created = _get_existing_problem(project_id, source_path, candidate.name)
    return _problem_generate_item(created)


def _create_non_python_problem(
    project_id: str,
    file_id: str,
    repo_path: Path,
    source_path: str,
    target: TargetSymbol,
    generated: dict,
) -> ProblemGenerateItem:
    now = _utc_now()
    problem_id = str(uuid.uuid4())
    context = collect_context(str(repo_path), source_path, target.symbol)
    context.update(_project_generation_context(project_id))
    if target.role_in_project:
        context["role_in_project"] = target.role_in_project
        context["depends_on"] = target.depends_on
        context["used_by"] = target.used_by
    context_json = json.dumps(context, ensure_ascii=False)
    tree_metadata = problem_tree_metadata(project_id, source_path, target.symbol, target)

    with engine.begin() as connection:
        connection.execute(
            insert(problems).values(
                id=problem_id,
                project_id=project_id,
                file_id=file_id,
                source_path=source_path,
                target_symbol=target.symbol,
                problem_type=target.problem_type,
                prompt=generated["prompt"],
                starter_code=generated["starter_code"],
                test_path=None,
                grading_method="llm",
                original_code=generated["original_code"],
                difficulty=target.difficulty,
                context=context_json,
                parent_id=tree_metadata["parent_id"],
                weight=tree_metadata["weight"],
                depth=tree_metadata["depth"],
                unlock_dependencies=json.dumps(tree_metadata["unlock_dependencies"], ensure_ascii=False),
                role_in_project=target.role_in_project,
                status=tree_metadata["status"],
                created_at=now,
                updated_at=now,
            )
        )

    created = _get_existing_problem(project_id, source_path, target.symbol)
    return _problem_generate_item(created)


def _is_dunder_method(name: str) -> bool:
    return name != "__call__" and name.startswith("__") and name.endswith("__")


def _decorators_are_allowed(node: ast.FunctionDef) -> bool:
    for decorator in node.decorator_list:
        name = _decorator_name(decorator)
        if name in {"property", "setter", "deleter"}:
            return False
        if name not in ALLOWED_DECORATORS:
            return False
    return True


def _decorator_name(decorator: ast.expr) -> str:
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    return ""


def _parameter_count(args: ast.arguments) -> int:
    return len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)


def _body_line_count(node: ast.FunctionDef) -> int:
    if not node.body:
        return 0
    return node.body[-1].end_lineno - node.body[0].lineno + 1


def _has_dangerous_direct_call(node: ast.FunctionDef) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        function = child.func
        if isinstance(function, ast.Name) and function.id in DANGEROUS_CALL_NAMES:
            return True
        if isinstance(function, ast.Attribute):
            root = _attribute_root(function)
            if root in DANGEROUS_CALL_ROOTS:
                return True
            if (root, function.attr) in DANGEROUS_CALL_ATTRIBUTES:
                return True
    return False


def _attribute_root(node: ast.Attribute) -> str:
    value = node.value
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else ""


def _has_type_hint(node: ast.FunctionDef) -> bool:
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    return any(arg.annotation is not None for arg in args) or node.returns is not None


def _has_many_external_dependencies(node: ast.FunctionDef) -> bool:
    local_names = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Param)):
            local_names.add(child.id)

    external_names = {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name)
        and isinstance(child.ctx, ast.Load)
        and child.id not in local_names
        and child.id not in IGNORED_EXTERNAL_NAMES
    }
    return len(external_names) > 8


def _function_signature(node: ast.FunctionDef) -> str:
    args = [_format_arg(arg) for arg in [*node.args.posonlyargs, *node.args.args]]
    args.extend(_format_arg(arg) for arg in node.args.kwonlyargs)
    signature = f"{node.name}({', '.join(args)})"
    if node.returns is not None:
        signature += f" -> {ast.unparse(node.returns)}"
    return signature


def _format_arg(arg: ast.arg) -> str:
    if arg.annotation is None:
        return arg.arg
    return f"{arg.arg}: {ast.unparse(arg.annotation)}"


def _replace_function_body(source: str, node: ast.FunctionDef) -> str:
    lines = source.splitlines(keepends=True)
    start_index = node.body[0].lineno - 1
    end_index = node.body[-1].end_lineno
    indent = " " * node.body[0].col_offset
    replacement = [
        f"{indent}# TODO: implement this function\n",
        f"{indent}raise NotImplementedError\n",
    ]
    lines[start_index:end_index] = replacement
    return "".join(lines)


def _extract_function_source(source: str, node: ast.FunctionDef) -> str:
    lines = source.splitlines(keepends=True)
    return "".join(lines[node.lineno - 1 : node.end_lineno])


def _resolve_source_file(repo_path: Path, source_path: str) -> Path:
    source_file = (repo_path / source_path).resolve()
    try:
        source_file.relative_to(repo_path.resolve())
    except ValueError:
        raise AppError("PROBLEM_NOT_FOUND", "Source file not found.")

    if not source_file.exists() or not source_file.is_file():
        raise AppError("PROBLEM_NOT_FOUND", "Source file not found.")
    return source_file


def _baseline_passes(repo_path: Path, test_path: str) -> bool:
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", test_path, "-q", "--tb=short"],
            cwd=repo_path,
            timeout=10,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _get_existing_problem(project_id: str, source_path: str, target_symbol: str | None = None):
    with engine.begin() as connection:
        query = select(problems).where(problems.c.project_id == project_id, problems.c.source_path == source_path)
        if target_symbol is not None:
            query = query.where(problems.c.target_symbol == target_symbol)
        return connection.execute(query).mappings().first()


def _get_file_row(project_id: str, source_path: str):
    with engine.begin() as connection:
        row = (
            connection.execute(select(files).where(files.c.project_id == project_id, files.c.source_path == source_path))
            .mappings()
            .first()
        )

    if row is None:
        raise AppError("PROBLEM_NOT_FOUND", "Source file is not registered for this project.")
    return row


def _mark_recommended_symbol_status(project_id: str, source_path: str, symbol: str, status: str, reason: str) -> None:
    file_row = _get_file_row(project_id, source_path)
    raw = file_row["recommended_symbols"]
    if not raw:
        return
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(values, list):
        return

    changed = False
    for item in values:
        if not isinstance(item, dict):
            continue
        item_symbol = str(item.get("symbol") or "")
        if item_symbol == symbol or item_symbol.endswith(f".{symbol}") or symbol.endswith(item_symbol):
            item["status"] = status
            item["status_reason"] = reason
            changed = True
    if not changed:
        return

    with engine.begin() as connection:
        connection.execute(
            update(files)
            .where(files.c.id == file_row["id"])
            .values(recommended_symbols=json.dumps(values, ensure_ascii=False), updated_at=_utc_now())
        )


def _project_generation_context(project_id: str) -> dict:
    project = _get_project_row(project_id)
    raw = project["project_summary"]
    if not raw:
        return {}
    try:
        summary = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(summary, dict):
        return {}
    return {"project_summary": summary.get("project_summary") or ""}


def _problem_generate_item(row) -> ProblemGenerateItem:
    return ProblemGenerateItem(
        problem_id=row["id"],
        file_id=row["file_id"],
        source_path=row["source_path"],
        target_symbol=row["target_symbol"],
        problem_type=row["problem_type"],
        test_path=row["test_path"],
        grading_method=row["grading_method"],
        difficulty=row["difficulty"],
    )


def _problem_list_item(row) -> ProblemListItem:
    raw_dependencies = row["unlock_dependencies"]
    try:
        dependencies = json.loads(raw_dependencies) if raw_dependencies else []
    except json.JSONDecodeError:
        dependencies = []
    if not isinstance(dependencies, list):
        dependencies = []
    return ProblemListItem(
        id=row["id"],
        source_path=row["source_path"],
        target_symbol=row["target_symbol"],
        status=row["status"] or "unlocked",
        grading_method=row["grading_method"],
        difficulty=row["difficulty"],
        problem_type=row["problem_type"],
        parent_id=row["parent_id"],
        depth=row["depth"] or 0,
        unlock_dependencies=[str(item) for item in dependencies],
        role_in_project=row["role_in_project"],
    )


def _problem_generate_response(items: list[ProblemGenerateItem]) -> ProblemGenerateResponse:
    first = items[0]
    return ProblemGenerateResponse(
        problems=items,
        problem_id=first.problem_id,
        file_id=first.file_id,
        source_path=first.source_path,
        target_symbol=first.target_symbol,
        problem_type=first.problem_type,
        test_path=first.test_path,
        grading_method=first.grading_method,
        difficulty=first.difficulty,
    )
