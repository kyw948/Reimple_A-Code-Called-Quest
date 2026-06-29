import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.core.errors import AppError
from app.db.database import engine, files, projects
from app.services import llm_client
from app.services.file_prefilter import prefilter_files, prefilter_reason_counts
from app.services.project_service import _get_project_row, _utc_now

try:
    from google.genai.errors import ClientError
except Exception:  # pragma: no cover - google-genai may be absent in tests.
    ClientError = None


MAX_LLM_FILE_BYTES = 50 * 1024
ASSESS_DELAY_SECONDS = 2
BATCH_SIZE = 3
MAX_BATCH_FILE_LINES = 100
QUICK_SCAN_BATCH_SIZE = 25
QUICK_SCAN_FILE_LINES = 10

BATCH_SYSTEM_INSTRUCTION = """너는 코딩 연습 문제 출제를 위한 소스 코드 분석기이다.
주어진 소스 파일들을 분석하여 각각 연습 문제로 만들기 적합한 함수를 찾아라.
반드시 JSON 배열로만 응답하라."""

QUICK_SCAN_SYSTEM_INSTRUCTION = """너는 AI 논문 코드의 파일 분류기이다.
각 파일이 코딩 연습 문제로 분석할 가치가 있는지 빠르게 판단하라.
반드시 JSON 배열로만 응답하라."""


class ProjectAssessRequest(BaseModel):
    source_paths: list[str] | None = None
    force: bool = False


class RecommendedSymbol(BaseModel):
    symbol: str
    difficulty: Literal["easy", "medium", "hard"]
    reason: str
    problem_type: Literal["function_blank", "function_partial"] = "function_blank"
    role_in_project: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    used_by: list[str] = Field(default_factory=list)


class FileAssessment(BaseModel):
    source_path: str
    suitable: bool | None
    reason: str
    recommended_symbols: list[RecommendedSymbol]


class ProjectAssessResponse(BaseModel):
    assessments: list[FileAssessment]
    prefilter_skipped: int = 0
    prefilter_reasons: dict[str, int] = Field(default_factory=dict)


class ProjectAssessStartResponse(BaseModel):
    status: str


class ProjectAssessStatusResponse(BaseModel):
    status: str
    total: int
    assessed: int
    suitable: int
    suitable_count: int
    progress: float
    deferred: list[dict[str, str]] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)


def assess_project_files(project_id: str, payload: ProjectAssessRequest) -> ProjectAssessResponse:
    project = _get_project_row(project_id)
    _update_assess_status(project_id, "running")
    repo_path = Path(project["repo_path"])
    requested_paths = payload.source_paths or _pending_target_source_paths(project_id)
    cached_assessments: list[FileAssessment] = []
    paths_to_assess: list[str] = []
    for source_path in requested_paths:
        cached = None if payload.force else _get_cached_assessment(project_id, source_path)
        if cached is None:
            paths_to_assess.append(source_path)
        else:
            cached_assessments.append(cached)

    suitable_paths, skipped = prefilter_files(project["repo_path"], paths_to_assess)
    skipped_assessments = [
        FileAssessment(
            source_path=str(item["path"]),
            suitable=False,
            reason=str(item["reason"]),
            recommended_symbols=[],
        )
        for item in skipped
    ]
    for assessment in skipped_assessments:
        _save_assessment(project_id, assessment)

    project_context = _project_context(project, suitable_paths)
    analyze_paths, quick_skipped_assessments = quick_scan(repo_path, suitable_paths, project_context=project_context)
    for assessment in quick_skipped_assessments:
        _save_assessment(project_id, assessment)

    batch_assessments: list[FileAssessment] = []
    for batch_index, start in enumerate(range(0, len(analyze_paths), BATCH_SIZE), start=1):
        batch_paths = analyze_paths[start : start + BATCH_SIZE]
        batch_results = _assess_batch_with_token_retry(repo_path, batch_paths, project_context=project_context)
        batch_results = _filter_by_actual_symbols(batch_results, project)
        for assessment in batch_results:
            _save_assessment(project_id, assessment)
        batch_assessments.extend(batch_results)
        print(f"[assess-bg] batch {batch_index} completed, saved {len(batch_results)} files")
        if start + BATCH_SIZE < len(analyze_paths):
            time.sleep(ASSESS_DELAY_SECONDS)

    ordered = _order_assessments(
        requested_paths,
        [*cached_assessments, *skipped_assessments, *quick_skipped_assessments, *batch_assessments],
    )
    response = ProjectAssessResponse(
        assessments=ordered,
        prefilter_skipped=len(skipped),
        prefilter_reasons=prefilter_reason_counts(skipped),
    )
    _update_assess_status(project_id, "completed")
    return response


def start_background_assess(project_id: str) -> ProjectAssessStartResponse:
    status = get_assess_status(project_id)
    if status.status == "running":
        return ProjectAssessStartResponse(status="running")
    if status.status == "completed" and status.assessed >= status.total:
        return ProjectAssessStartResponse(status="completed")

    _update_assess_status(project_id, "running")
    thread = threading.Thread(target=_run_assess_background, args=(project_id,), daemon=True)
    thread.start()
    return ProjectAssessStartResponse(status="started")


def get_assess_status(project_id: str) -> ProjectAssessStatusResponse:
    project = _get_project_row(project_id)
    with engine.begin() as connection:
        total = connection.execute(
            select(func.count()).select_from(files).where(files.c.project_id == project_id, files.c.is_target == 1)
        ).scalar_one()
        assessed = connection.execute(
            select(func.count()).select_from(files).where(
                files.c.project_id == project_id,
                files.c.is_target == 1,
                (files.c.suitable.is_not(None) | files.c.suitable_reason.is_not(None)),
            )
        ).scalar_one()
        suitable = connection.execute(
            select(func.count()).select_from(files).where(
                files.c.project_id == project_id,
                files.c.is_target == 1,
                files.c.suitable == 1,
            )
        ).scalar_one()
        deferred_rows = connection.execute(
            select(files.c.source_path, files.c.suitable_reason).where(
                files.c.project_id == project_id,
                files.c.is_target == 1,
                files.c.suitable.is_(None),
                files.c.suitable_reason.is_not(None),
            )
        ).mappings().all()
    stored_status = str(project.get("assess_status") or "pending")
    status = "completed" if total > 0 and assessed >= total else stored_status
    if total == 0 and stored_status == "running":
        status = "completed"
    progress = assessed / total if total > 0 else 0.0
    candidates = _assessed_candidates(project_id)
    print(f"[assess-status] returning {len(candidates)} candidates, status={status}")
    return ProjectAssessStatusResponse(
        status=status,
        total=total,
        assessed=assessed,
        suitable=suitable,
        suitable_count=suitable,
        progress=progress,
        deferred=[
            {"source_path": row["source_path"], "reason": row["suitable_reason"] or "분석 보류"}
            for row in deferred_rows
        ],
        candidates=candidates,
    )


def _run_assess_background(project_id: str) -> None:
    try:
        assess_project_files(project_id, ProjectAssessRequest())
    except Exception as exc:
        _update_assess_status(project_id, f"error: {exc}")


def quick_scan(
    repo_path: Path,
    source_paths: list[str],
    project_context: dict[str, Any] | None = None,
    batch_size: int = QUICK_SCAN_BATCH_SIZE,
) -> tuple[list[str], list[FileAssessment]]:
    analyze_paths: list[str] = []
    skipped: list[FileAssessment] = []
    for index in range(0, len(source_paths), batch_size):
        batch = source_paths[index : index + batch_size]
        batch_analyze, batch_skipped = _quick_scan_batch_with_token_retry(repo_path, batch, project_context)
        analyze_paths.extend(batch_analyze)
        skipped.extend(batch_skipped)
        if index + batch_size < len(source_paths):
            time.sleep(ASSESS_DELAY_SECONDS)
    return analyze_paths, skipped


def _quick_scan_batch_with_token_retry(
    repo_path: Path,
    paths: list[str],
    project_context: dict[str, Any] | None,
) -> tuple[list[str], list[FileAssessment]]:
    try:
        return _quick_scan_batch(repo_path, paths, project_context)
    except Exception as exc:
        if _is_rate_limit_error(exc):
            time.sleep(_wait_seconds_from_error(exc))
            try:
                return _quick_scan_batch(repo_path, paths, project_context)
            except Exception:
                return [], [_deferred(source_path, "rate limit으로 분석 보류") for source_path in paths]
        if not _is_token_limit_error(exc):
            return [], [_deferred(source_path, f"분석 오류: {str(exc)[:80]}") for source_path in paths]
        if len(paths) == 1:
            return [], [_deferred(paths[0], "파일이 커서 분석 보류")]

    analyze_paths: list[str] = []
    skipped: list[FileAssessment] = []
    for source_path in paths:
        try:
            batch_analyze, batch_skipped = _quick_scan_batch(repo_path, [source_path], project_context)
            analyze_paths.extend(batch_analyze)
            skipped.extend(batch_skipped)
        except Exception as exc:
            if _is_token_limit_error(exc):
                skipped.append(_deferred(source_path, "파일이 커서 분석 보류"))
            elif _is_rate_limit_error(exc):
                skipped.append(_deferred(source_path, "rate limit으로 분석 보류"))
            else:
                skipped.append(_deferred(source_path, f"분석 오류: {str(exc)[:80]}"))
    return analyze_paths, skipped


def _quick_scan_batch(
    repo_path: Path,
    paths: list[str],
    project_context: dict[str, Any] | None,
) -> tuple[list[str], list[FileAssessment]]:
    if not paths:
        return [], []

    file_headers: list[tuple[str, str]] = []
    for source_path in paths:
        source_file = _resolve_source_file(repo_path, source_path)
        header = "\n".join(source_file.read_text(encoding="utf-8", errors="ignore").splitlines()[:QUICK_SCAN_FILE_LINES])
        file_headers.append((source_path, header))

    parsed_response = llm_client.call_gemini_with_validation(
        _build_quick_scan_prompt(file_headers, project_context),
        QUICK_SCAN_SYSTEM_INSTRUCTION,
        {"source_path": str},
        max_retries=1,
    )
    if parsed_response is None:
        return paths, []
    parsed = _normalize_assessment_array(parsed_response, paths)
    by_path = {item.get("source_path"): item for item in parsed if isinstance(item, dict)}

    analyze_paths: list[str] = []
    skipped: list[FileAssessment] = []
    for source_path in paths:
        item = by_path.get(source_path)
        if item is None:
            analyze_paths.append(source_path)
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        if not verdict and "suitable" in item:
            analyze_paths.append(source_path)
        elif verdict == "skip":
            skipped.append(_unsuitable(source_path, str(item.get("reason") or "빠른 스캔에서 제외됨")))
        else:
            analyze_paths.append(source_path)
    return analyze_paths, skipped


def assess_files(
    repo_path: Path,
    source_paths: list[str],
    batch_size: int = BATCH_SIZE,
    project_context: dict[str, Any] | None = None,
) -> list[FileAssessment]:
    results: list[FileAssessment] = []
    for index in range(0, len(source_paths), batch_size):
        batch = source_paths[index : index + batch_size]
        results.extend(_assess_batch_with_token_retry(repo_path, batch, project_context=project_context))
        if index + batch_size < len(source_paths):
            time.sleep(ASSESS_DELAY_SECONDS)
    return results


def _assess_batch_with_token_retry(
    repo_path: Path,
    paths: list[str],
    project_context: dict[str, Any] | None = None,
) -> list[FileAssessment]:
    try:
        return _assess_batch(repo_path, paths, project_context=project_context)
    except Exception as exc:
        if _is_rate_limit_error(exc):
            wait = _wait_seconds_from_error(exc)
            print(f"Rate limited. Waiting {wait}s...")
            time.sleep(wait)
            try:
                return _assess_batch(repo_path, paths, project_context=project_context)
            except Exception:
                return [_deferred(source_path, "rate limit으로 분석 보류") for source_path in paths]
        if not _is_token_limit_error(exc):
            time.sleep(5)
            try:
                return _assess_batch(repo_path, paths, project_context=project_context)
            except Exception:
                return [_deferred(source_path, f"분석 오류: {str(exc)[:80]}") for source_path in paths]
        if len(paths) == 1:
            return [_deferred(paths[0], "파일이 커서 분석 보류")]

    results: list[FileAssessment] = []
    for source_path in paths:
        try:
            results.extend(_assess_batch(repo_path, [source_path], project_context=project_context))
        except Exception as exc:
            if _is_token_limit_error(exc):
                results.append(_deferred(source_path, "파일이 커서 분석 보류"))
            elif _is_rate_limit_error(exc):
                results.append(_deferred(source_path, "rate limit으로 분석 보류"))
            else:
                results.append(_deferred(source_path, f"분석 오류: {str(exc)[:80]}"))
    return results


def _assess_batch(repo_path: Path, paths: list[str], project_context: dict[str, Any] | None = None) -> list[FileAssessment]:
    if not paths:
        return []

    file_contents: list[tuple[str, str]] = []
    for source_path in paths:
        source_file = _resolve_source_file(repo_path, source_path)
        content = "\n".join(source_file.read_text(encoding="utf-8", errors="ignore").splitlines()[:MAX_BATCH_FILE_LINES])
        file_contents.append((source_path, content))

    try:
        parsed_response = llm_client.call_gemini_with_validation(
            _build_batch_prompt(file_contents, project_context),
            BATCH_SYSTEM_INSTRUCTION,
            {"source_path": str, "suitable": bool},
            max_retries=1,
        )
        if parsed_response is None:
            return [_deferred(source_path, "분석 오류: LLM 응답을 받지 못했습니다") for source_path in paths]
        parsed = _normalize_assessment_array(parsed_response, paths)
    except AppError:
        raise
    except (json.JSONDecodeError, ValueError):
        return [_unsuitable(source_path, "LLM 응답을 JSON으로 파싱하지 못했습니다") for source_path in paths]

    by_path = {item.get("source_path"): item for item in parsed if isinstance(item, dict)}
    assessments: list[FileAssessment] = []
    for source_path in paths:
        item = by_path.get(source_path)
        if item is None:
            assessments.append(_unsuitable(source_path, "LLM 응답에서 파일 결과가 누락되었습니다"))
            continue
        assessments.append(
            FileAssessment(
                source_path=source_path,
                suitable=bool(item.get("suitable", False)),
                reason=str(item.get("reason") or "판정 사유 없음"),
                recommended_symbols=_recommended_symbols(item.get("recommended_symbols")),
            )
        )
    return assessments


def _resolve_source_file(repo_path: Path, source_path: str) -> Path:
    source_file = (repo_path / source_path).resolve()
    if not _is_inside_repo(source_file, repo_path) or not source_file.exists() or not source_file.is_file():
        raise AppError("INVALID_REPO_PATH", "Source file does not exist.")
    return source_file


def _build_batch_prompt(file_contents: list[tuple[str, str]], project_context: dict[str, Any] | None = None) -> str:
    blocks = []
    for index, (source_path, content) in enumerate(file_contents, start=1):
        blocks.append(f"===== 파일 {index}: {source_path} =====\n{content}")
    joined_blocks = "\n\n".join(blocks)
    context_section = _format_project_context(project_context)
    intro = (
        "아래는 AI 논문 구현 repository의 소스 파일들입니다.\n\n"
        f"{context_section}\n\n"
        "## 소스 파일들\n"
        if context_section
        else ""
    )
    return f"""{intro}아래 소스 파일들을 각각 분석하여 연습 문제로 적합한 함수를 찾아주세요.

{joined_blocks}

적합한 함수의 기준:
- 명확한 입출력이 있는 함수
- 알고리즘이나 로직이 포함된 함수
- 다른 파일 의존성이 적은 함수
- body가 3줄 이상인 함수

부적합한 경우:
- 설정/config만 있는 파일
- 외부 서비스 의존이 강한 함수
- body가 너무 단순한 함수 (단순 return, 위임)
- 특수 메서드 (__init__, __del__ 등)

아래 JSON 배열로 응답하세요:
[
  {{
    "source_path": "파일 경로 (위에서 준 경로 그대로)",
    "suitable": true,
    "reason": "판정 사유 한 줄",
    "recommended_symbols": [
      {{
        "symbol": "함수명",
        "difficulty": "easy/medium/hard",
        "problem_type": "function_blank/function_partial",
        "reason": "추천 사유",
        "role_in_project": "이 함수가 AI 논문 구현 전체에서 맡는 역할",
        "depends_on": ["먼저 이해하거나 구현해야 하는 내부 심볼"],
        "used_by": ["이 심볼을 사용하는 상위 심볼"]
      }}
    ]
  }}
]

적합한 함수가 없는 파일은 suitable=false, recommended_symbols=[]로 응답하세요.
모든 파일에 대해 빠짐없이 응답하세요."""


def _build_quick_scan_prompt(file_headers: list[tuple[str, str]], project_context: dict[str, Any] | None = None) -> str:
    blocks = []
    for index, (source_path, header) in enumerate(file_headers, start=1):
        blocks.append(f"{index}. {source_path}\n{header}")
    project_summary = _project_summary_line(project_context)
    return f"""아래 파일들이 코딩 연습 문제 출제를 위해 상세 분석할 가치가 있는지 판단해주세요.
설정 파일, 초기화 파일, 단순 임포트만 있는 파일은 "skip"으로 분류하세요.
모델 구현, 학습 로직, 데이터 처리 등 실질적 코드가 있는 파일만 "analyze"로 분류하세요.

프로젝트: {project_summary}

파일 목록:
{chr(10).join(blocks)}

JSON 배열로 응답:
[
  {{"source_path": "파일경로", "verdict": "analyze 또는 skip", "reason": "한 줄 사유"}}
]"""


def _parse_assessment_json(raw_response: str) -> dict[str, Any]:
    text = raw_response.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Assessment response must be an object.")
    return parsed


def _normalize_assessment_array(parsed: dict | list, requested_paths: list[str]) -> list[dict[str, Any]]:
    if isinstance(parsed, dict):
        if len(requested_paths) == 1:
            parsed.setdefault("source_path", requested_paths[0])
            return [parsed]
        raise ValueError("Batch assessment response must be an array.")
    if not isinstance(parsed, list):
        raise ValueError("Batch assessment response must be an array.")
    return [item for item in parsed if isinstance(item, dict)]


def _unsuitable(source_path: str, reason: str) -> FileAssessment:
    return FileAssessment(source_path=source_path, suitable=False, reason=reason, recommended_symbols=[])


def _deferred(source_path: str, reason: str) -> FileAssessment:
    return FileAssessment(source_path=source_path, suitable=None, reason=reason, recommended_symbols=[])


def _order_assessments(source_paths: list[str], assessments: list[FileAssessment]) -> list[FileAssessment]:
    by_path = {assessment.source_path: assessment for assessment in assessments}
    return [by_path[path] for path in source_paths if path in by_path]


def _pending_target_source_paths(project_id: str) -> list[str]:
    with engine.begin() as connection:
        rows = (
            connection.execute(
                select(files.c.source_path)
                .where(files.c.project_id == project_id, files.c.is_target == 1, files.c.status == "pending")
                .order_by(files.c.source_path.asc())
            )
            .scalars()
            .all()
        )
    return [path for path in rows if not Path(path).name.startswith("test_")]


def _get_cached_assessment(project_id: str, source_path: str) -> FileAssessment | None:
    with engine.begin() as connection:
        row = (
            connection.execute(
                select(files.c.suitable, files.c.suitable_reason, files.c.recommended_symbols).where(
                    files.c.project_id == project_id,
                    files.c.source_path == source_path,
                    files.c.suitable.is_not(None),
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    return FileAssessment(
        source_path=source_path,
        suitable=bool(row["suitable"]),
        reason=row["suitable_reason"] or "캐시된 분석 결과",
        recommended_symbols=_recommended_symbols(_load_recommended_symbols(row["recommended_symbols"])),
    )


def _load_recommended_symbols(raw: str | None) -> Any:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _recommended_symbols(value: Any) -> list[RecommendedSymbol]:
    if not isinstance(value, list):
        return []

    symbols: list[RecommendedSymbol] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        difficulty = str(item.get("difficulty") or "medium").strip()
        reason = str(item.get("reason") or "").strip()
        if not symbol:
            continue
        if difficulty not in {"easy", "medium", "hard"}:
            difficulty = "medium"
        problem_type = str(item.get("problem_type") or "function_blank").strip()
        if problem_type not in {"function_blank", "function_partial"}:
            problem_type = "function_blank"
        depends_on = item.get("depends_on")
        used_by = item.get("used_by")
        symbols.append(
            RecommendedSymbol(
                symbol=symbol,
                difficulty=cast(Literal["easy", "medium", "hard"], difficulty),
                reason=reason,
                problem_type=cast(Literal["function_blank", "function_partial"], problem_type),
                role_in_project=str(item.get("role_in_project") or "").strip() or None,
                depends_on=[str(value) for value in depends_on] if isinstance(depends_on, list) else [],
                used_by=[str(value) for value in used_by] if isinstance(used_by, list) else [],
            )
        )
    return symbols


def _filter_by_actual_symbols(assessments: list[FileAssessment], project) -> list[FileAssessment]:
    dependency_graph = _load_json_object(project["dependency_graph"])
    actual_symbols = dependency_graph.get("symbols")
    if not isinstance(actual_symbols, dict) or not actual_symbols:
        return assessments

    filtered_assessments: list[FileAssessment] = []
    for assessment in assessments:
        if not assessment.recommended_symbols:
            filtered_assessments.append(assessment)
            continue

        filtered_symbols: list[RecommendedSymbol] = []
        for symbol in assessment.recommended_symbols:
            actual_name = _match_actual_symbol(actual_symbols, assessment.source_path, symbol.symbol)
            if actual_name is None:
                print(f"[filter] {assessment.source_path}::{symbol.symbol} not found in actual symbols, skipping")
                continue
            filtered_symbols.append(symbol.model_copy(update={"symbol": actual_name}))

        filtered_assessments.append(
            assessment.model_copy(
                update={
                    "recommended_symbols": filtered_symbols,
                    "suitable": assessment.suitable if filtered_symbols else False,
                    "reason": assessment.reason if filtered_symbols else "실제 소스에서 추천 심볼을 찾지 못했습니다",
                }
            )
        )
    return filtered_assessments


def _match_actual_symbol(actual_symbols: dict[str, Any], source_path: str, symbol_name: str) -> str | None:
    exact_key = f"{source_path}::{symbol_name}"
    if exact_key in actual_symbols:
        return symbol_name

    source_prefix = f"{source_path}::"
    symbol_base = symbol_name.split(".")[0]
    for actual_key in actual_symbols:
        if not actual_key.startswith(source_prefix):
            continue
        actual_name = actual_key.split("::", 1)[1]
        if (
            actual_name == symbol_name
            or actual_name.startswith(symbol_base)
            or symbol_name in actual_name
            or actual_name in symbol_name
        ):
            return actual_name
    return None


def _assessed_candidates(project_id: str) -> list[dict[str, Any]]:
    from app.services.problem_tree import prepare_practice

    prepared = prepare_practice(project_id)
    return [candidate.model_dump() for candidate in prepared.candidates]


def _project_context(project, source_paths: list[str]) -> dict[str, Any] | None:
    project_summary = _load_json_object(project["project_summary"])
    architecture = _load_json_object(project["architecture"])
    dependency_graph = _load_json_object(project["dependency_graph"])
    if not project_summary and not architecture and not dependency_graph:
        return None

    return {
        "project_summary": project_summary,
        "modules": _modules_for_files(architecture, source_paths),
        "file_dependencies": _file_dependencies_for_files(architecture, source_paths),
        "dependencies": _dependencies_for_files(dependency_graph, source_paths),
        "implementation_order": _implementation_order_for_files(dependency_graph, source_paths),
    }


def _format_project_context(project_context: dict[str, Any] | None) -> str:
    if not project_context:
        return ""
    sections: list[str] = []
    if project_context.get("project_summary"):
        sections.append(
            "## 프로젝트 개요\n"
            f"{json.dumps(project_context['project_summary'], ensure_ascii=False, indent=2)}"
        )
    if project_context.get("modules"):
        sections.append(
            "## 이 파일들이 속한 모듈\n"
            f"{json.dumps(project_context['modules'], ensure_ascii=False, indent=2)}"
        )
    if project_context.get("file_dependencies"):
        sections.append(
            "## 파일 의존성\n"
            f"{json.dumps(project_context['file_dependencies'], ensure_ascii=False, indent=2)}"
        )
    dependency_info = {
        "dependencies": project_context.get("dependencies") or {},
        "implementation_order": project_context.get("implementation_order") or [],
    }
    if dependency_info["dependencies"] or dependency_info["implementation_order"]:
        sections.append(
            "## 의존성 정보\n"
            f"{json.dumps(dependency_info, ensure_ascii=False, indent=2)}"
        )
    return "\n\n".join(sections)


def _project_summary_line(project_context: dict[str, Any] | None) -> str:
    if not project_context:
        return "없음"
    summary = project_context.get("project_summary")
    if not isinstance(summary, dict):
        return "없음"
    return str(summary.get("project_summary") or summary.get("main_contribution") or "없음")


def _modules_for_files(architecture: dict[str, Any], source_paths: list[str]) -> dict[str, Any]:
    modules = architecture.get("modules") if isinstance(architecture, dict) else None
    if not isinstance(modules, dict):
        return {}
    source_set = set(source_paths)
    matched: dict[str, Any] = {}
    for module_name, module in modules.items():
        if not isinstance(module, dict):
            continue
        module_files = module.get("files")
        if not isinstance(module_files, list):
            continue
        included = [path for path in module_files if path in source_set]
        if included:
            matched[str(module_name)] = {
                "description": module.get("description") or "",
            }
    return matched


def _file_dependencies_for_files(architecture: dict[str, Any], source_paths: list[str]) -> dict[str, list[str]]:
    dependencies = architecture.get("file_dependencies") if isinstance(architecture, dict) else None
    if not isinstance(dependencies, dict):
        return {}
    source_set = set(source_paths)
    matched: dict[str, list[str]] = {}
    for file_path, values in dependencies.items():
        if file_path not in source_set:
            continue
        matched[str(file_path)] = [str(value) for value in values] if isinstance(values, list) else []
    return matched


def _dependencies_for_files(dependency_graph: dict[str, Any], source_paths: list[str]) -> dict[str, list[str]]:
    dependencies = dependency_graph.get("dependencies") if isinstance(dependency_graph, dict) else None
    if not isinstance(dependencies, dict):
        return {}
    matched: dict[str, list[str]] = {}
    for symbol, values in dependencies.items():
        file_path = str(symbol).split("::", 1)[0]
        if file_path not in source_paths:
            continue
        matched[str(symbol)] = [str(value) for value in values] if isinstance(values, list) else []
    return matched


def _implementation_order_for_files(dependency_graph: dict[str, Any], source_paths: list[str]) -> list[dict[str, Any]]:
    order = dependency_graph.get("implementation_order") if isinstance(dependency_graph, dict) else None
    if not isinstance(order, list):
        return []
    source_set = set(source_paths)
    return [item for item in order if isinstance(item, dict) and item.get("file") in source_set]


def _load_json_object(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_token_limit_error(exc: Exception) -> bool:
    if ClientError is not None and isinstance(exc, ClientError):
        text = str(exc).lower()
        return "token count exceeds" in text or "input token" in text
    text = str(exc).lower()
    return "token count exceeds" in text or "input token count exceeds" in text


def _is_rate_limit_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    text = str(exc).lower()
    return status_code == 429 or "429" in text or "resource_exhausted" in text or "rate limit" in text


def _wait_seconds_from_error(exc: Exception) -> int:
    text = str(exc)
    match = re.search(r"(\d+)\.?\d*s", text)
    return int(match.group(1)) + 5 if match else 60


def _save_assessment(project_id: str, assessment: FileAssessment) -> None:
    now = _utc_now()
    with engine.begin() as connection:
        file_row = connection.execute(
            select(files.c.id).where(files.c.project_id == project_id, files.c.source_path == assessment.source_path)
        ).mappings().first()
        if file_row is None:
            return

        connection.execute(
            update(files)
            .where(files.c.id == file_row["id"])
            .values(
                suitable=None if assessment.suitable is None else 1 if assessment.suitable else 0,
                suitable_reason=assessment.reason,
                recommended_symbols=json.dumps(
                    [symbol.model_dump() for symbol in assessment.recommended_symbols],
                    ensure_ascii=False,
                ),
                updated_at=now,
            )
        )


def _update_assess_status(project_id: str, status: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(projects)
            .where(projects.c.id == project_id)
            .values(assess_status=status, updated_at=_utc_now())
        )


def _is_inside_repo(path: Path, repo_path: Path) -> bool:
    try:
        path.relative_to(repo_path.resolve())
        return True
    except ValueError:
        return False
