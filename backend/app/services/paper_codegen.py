import ast
import json
import re
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.core.errors import AppError
from app.db.database import engine, projects
from app.services import llm_client


CODEGEN_SYSTEM = """너는 AI 논문을 PyTorch 코드로 구현하는 전문가이다.
주어진 명세에 따라 정확한 Python 코드를 작성하라.
마크다운 코드블록 없이 순수 Python 코드만 반환하라."""


class PaperCodegenStartResponse(BaseModel):
    status: str


class PaperCodegenStatusResponse(BaseModel):
    status: str
    total_files: int = 0
    generated_files: int = 0
    current_file: str | None = None
    progress: float = 0.0
    generated_repo_path: str | None = None
    files: list[str] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def start_code_generation(project_id: str) -> PaperCodegenStartResponse:
    project = _get_project(project_id)
    if not project["paper_source"]:
        raise AppError("INVALID_PROJECT_MODE", "논문 기반 프로젝트만 코드 생성을 실행할 수 있습니다.")
    if project["analysis_status"] not in {"planned", "code_generated"}:
        raise AppError("PAPER_PLAN_REQUIRED", "먼저 구조 설계를 완료하세요.")

    existing_path = project["generated_repo_path"]
    if project["analysis_status"] == "code_generated" and existing_path and Path(existing_path).exists():
        _set_job(
            project_id,
            {
                "status": "completed",
                "total_files": 0,
                "generated_files": 0,
                "current_file": None,
                "progress": 1.0,
                "generated_repo_path": existing_path,
                "files": _list_generated_files(existing_path),
                "errors": [],
            },
        )
        return PaperCodegenStartResponse(status="completed")

    with _jobs_lock:
        current = _jobs.get(project_id)
        if current and current.get("status") == "running":
            return PaperCodegenStartResponse(status="started")

    architecture = _loads(project["architecture"])
    logic_design = _loads(project["dependency_graph"])
    files = _ordered_files(architecture, logic_design)
    _set_job(
        project_id,
        {
            "status": "running",
            "total_files": len(files),
            "generated_files": 0,
            "current_file": None,
            "progress": 0.0,
            "generated_repo_path": None,
            "files": [],
            "errors": [],
        },
    )

    thread = threading.Thread(target=_run_codegen_background, args=(project_id,), daemon=True)
    thread.start()
    return PaperCodegenStartResponse(status="started")


def get_codegen_status(project_id: str) -> PaperCodegenStatusResponse:
    project = _get_project(project_id)
    with _jobs_lock:
        job = dict(_jobs.get(project_id) or {})

    if not job and project["analysis_status"] == "code_generated" and project["generated_repo_path"]:
        job = {
            "status": "completed",
            "total_files": 0,
            "generated_files": 0,
            "current_file": None,
            "progress": 1.0,
            "generated_repo_path": project["generated_repo_path"],
            "files": _list_generated_files(project["generated_repo_path"]),
            "errors": [],
        }

    if not job:
        job = {
            "status": "idle",
            "total_files": 0,
            "generated_files": 0,
            "current_file": None,
            "progress": 0.0,
            "generated_repo_path": project["generated_repo_path"],
            "files": [],
            "errors": [],
        }

    return PaperCodegenStatusResponse(**job)


def _run_codegen_background(project_id: str) -> None:
    try:
        project = _get_project(project_id)
        architecture = _loads(project["architecture"])
        logic_design = _loads(project["dependency_graph"])
        overall_plan = _loads(project["project_summary"])
        files = _ordered_files(architecture, logic_design)
        generated_files: dict[str, str] = {}
        errors: list[dict[str, str]] = []

        for file_info in files:
            file_path = str(file_info.get("path") or "").strip()
            if not file_path:
                continue
            _update_job(project_id, current_file=file_path)
            try:
                code = _generate_file_code(project, overall_plan, architecture, logic_design, file_info, generated_files)
                generated_files[file_path] = code
            except Exception as exc:
                errors.append({"path": file_path, "message": str(exc)})
            _update_job(
                project_id,
                generated_files=len(generated_files),
                progress=(len(generated_files) + len(errors)) / max(len(files), 1),
                files=sorted(generated_files),
                errors=errors,
            )

        if not generated_files:
            _update_project(project_id, analysis_status="error")
            _update_job(project_id, status="error", current_file=None, errors=errors)
            return

        repo_path = save_generated_code(project_id, generated_files)
        _update_project(
            project_id,
            repo_path=repo_path,
            generated_repo_path=repo_path,
            analysis_status="code_generated",
        )
        _update_job(
            project_id,
            status="completed",
            current_file=None,
            progress=1.0,
            generated_repo_path=repo_path,
            files=sorted(generated_files),
            errors=errors,
        )
    except Exception as exc:
        _update_project(project_id, analysis_status="error")
        _update_job(project_id, status="error", current_file=None, errors=[{"path": "", "message": str(exc)}])


def _generate_file_code(project, overall_plan, architecture, logic_design, file_info, generated_files) -> str:
    file_path = str(file_info["path"])
    references = get_reference_code(file_path, generated_files, architecture)
    prompt = _build_codegen_prompt(project, overall_plan, logic_design, file_info, references)
    raw = llm_client.call_gemini(prompt, CODEGEN_SYSTEM, response_mime_type=None)
    code = extract_code(raw)
    syntax_error = _syntax_error(code)
    if not syntax_error:
        return code

    retry_prompt = f"{prompt}\n\n이전 시도에서 아래 Python 구문 오류가 발생했습니다. 수정해주세요:\n{syntax_error}"
    raw = llm_client.call_gemini(retry_prompt, CODEGEN_SYSTEM, response_mime_type=None)
    code = extract_code(raw)
    syntax_error = _syntax_error(code)
    if syntax_error:
        raise AppError("PAPER_CODEGEN_FILE_FAILED", f"{file_path}: Python 구문 오류: {syntax_error}")
    return code


def _build_codegen_prompt(project, overall_plan, logic_design, file_info, references) -> str:
    file_path = str(file_info.get("path") or "")
    specs = _specifications_for_file(logic_design, file_path)
    reference_blocks = "\n\n".join(
        f"### {item['path']}\n```python\n{item['code']}\n```" for item in references
    ) or "없음"
    return f"""아래 명세에 따라 {file_path} 파일의 Python 코드를 작성해주세요.

## 논문
{project["paper_title"] or ""}
{(project["paper_abstract"] or "")[:500]}

## 구현 계획
{overall_plan.get("summary") or ""}
핵심 알고리즘: {", ".join(overall_plan.get("key_algorithms") or [])}

## 이 파일의 명세
파일: {file_path}
설명: {file_info.get("description") or ""}
클래스: {file_info.get("classes") or []}
함수: {file_info.get("functions") or []}

상세 명세:
{json.dumps(specs, ensure_ascii=False, indent=2)}

## 이미 구현된 의존 파일
{reference_blocks}

## 규칙
- PyTorch 기반
- import는 필요한 것만 포함
- 이미 구현된 파일의 클래스/함수를 import하여 사용
- type hint 포함
- docstring 포함
- 논문의 수식/알고리즘에 충실하게 구현
- 순수 Python 코드만 반환
"""


def get_reference_code(file_path: str, generated_files: dict[str, str], architecture: dict[str, Any]) -> list[dict[str, str]]:
    file_info = _find_file_info(architecture, file_path)
    deps = file_info.get("depends_on", []) if file_info else []
    references: list[dict[str, str]] = []
    for dep_path in deps:
        if dep_path not in generated_files:
            continue
        code = generated_files[dep_path]
        if code.count("\n") > 200:
            code = extract_signatures(code)
        references.append({"path": dep_path, "code": code})
    return references


def extract_code(response_text: str) -> str:
    text = response_text.strip()
    if text.startswith("```python"):
        text = text[len("```python") :].strip()
    if text.startswith("```"):
        text = text[3:].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    return text.strip()


def extract_signatures(code: str) -> str:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "\n".join(code.splitlines()[:80])
    lines = code.splitlines()
    snippets: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            snippets.append(lines[node.lineno - 1])
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            end = min(getattr(node, "end_lineno", node.lineno), node.lineno + 20)
            snippets.extend(lines[node.lineno - 1 : end])
    return "\n".join(snippets)


def save_generated_code(project_id: str, generated_files: dict[str, str]) -> str:
    base_dir = Path.home() / ".codepractice" / "generated" / project_id
    base_dir.mkdir(parents=True, exist_ok=True)
    for file_path, code in generated_files.items():
        relative = Path(file_path)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        full_path = base_dir / relative
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(code, encoding="utf-8")
    (base_dir / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n', encoding="utf-8")
    return str(base_dir)


def _ordered_files(architecture: dict[str, Any], logic_design: dict[str, Any]) -> list[dict[str, Any]]:
    files = architecture.get("files") if isinstance(architecture, dict) else []
    if not isinstance(files, list):
        return []
    by_path = {str(item.get("path")): item for item in files if isinstance(item, dict) and item.get("path")}
    ordered: list[dict[str, Any]] = []
    implementation_order = logic_design.get("implementation_order", []) if isinstance(logic_design, dict) else []
    for path in implementation_order:
        if path in by_path:
            ordered.append(by_path[path])
    for path, item in by_path.items():
        if item not in ordered:
            ordered.append(item)
    return ordered


def _specifications_for_file(logic_design: dict[str, Any], file_path: str) -> list[dict[str, Any]]:
    specs = logic_design.get("specifications") if isinstance(logic_design, dict) else []
    if not isinstance(specs, list):
        return []
    return [item for item in specs if isinstance(item, dict) and item.get("file") == file_path]


def _find_file_info(architecture: dict[str, Any], file_path: str) -> dict[str, Any] | None:
    files = architecture.get("files") if isinstance(architecture, dict) else []
    if not isinstance(files, list):
        return None
    return next((item for item in files if isinstance(item, dict) and item.get("path") == file_path), None)


def _syntax_error(code: str) -> str | None:
    try:
        compile(code, "<generated>", "exec")
    except SyntaxError as exc:
        return f"{exc.msg} at line {exc.lineno}"
    return None


def _get_project(project_id: str):
    with engine.begin() as connection:
        row = connection.execute(select(projects).where(projects.c.id == project_id)).mappings().first()
    if row is None:
        raise AppError("PROJECT_NOT_FOUND", "Project not found.")
    return row


def _update_project(project_id: str, **values) -> None:
    with engine.begin() as connection:
        connection.execute(update(projects).where(projects.c.id == project_id).values(**values))


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _set_job(project_id: str, job: dict[str, Any]) -> None:
    with _jobs_lock:
        _jobs[project_id] = job


def _update_job(project_id: str, **updates) -> None:
    with _jobs_lock:
        job = _jobs.setdefault(project_id, {})
        job.update(updates)


def _list_generated_files(repo_path: str) -> list[str]:
    root = Path(repo_path)
    if not root.exists():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*.py"))
