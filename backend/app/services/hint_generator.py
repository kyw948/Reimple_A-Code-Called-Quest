import ast
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import select

from app.core.errors import AppError
from app.db.database import engine, problems
from app.services import llm_client
from app.services.project_service import _get_project_row


SYSTEM_INSTRUCTION = """너는 코딩 연습 도우미이다.
학습자에게 힌트를 제공하되, 절대 정답 코드를 직접 알려주지 마라.
힌트는 한국어 마크다운으로 작성하라."""


class ProblemHintRequest(BaseModel):
    level: int


class ProblemHintResponse(BaseModel):
    level: int
    hint: str
    format: str = "markdown"


def generate_hint(problem_id: str, payload: ProblemHintRequest) -> ProblemHintResponse:
    if payload.level not in {1, 2, 3}:
        raise AppError("INVALID_HINT_LEVEL", "level은 1~3 사이여야 합니다")

    problem = _get_problem_row(problem_id)
    project = _get_project_row(problem["project_id"])
    original_code = _problem_original_code(problem, Path(project["repo_path"]))

    try:
        hint = _call_hint_with_retry(_build_prompt(payload.level, problem["target_symbol"], original_code))
    except AppError as exc:
        if exc.detail.get("error_code") == "LLM_API_KEY_MISSING":
            raise AppError("LLM_API_KEY_MISSING", "힌트 생성을 위해 GEMINI_API_KEY가 필요합니다") from exc
        raise

    return ProblemHintResponse(level=payload.level, hint=hint.strip(), format="markdown")


def _call_hint_with_retry(prompt: str) -> str:
    for attempt in range(2):
        hint = llm_client.call_gemini(prompt, SYSTEM_INSTRUCTION, response_mime_type=None).strip()
        if hint:
            return hint
        prompt = f"{prompt}\n\n빈 응답을 반환하지 말고, 반드시 한국어 마크다운 힌트를 작성하세요."
    return ""


def _get_problem_row(problem_id: str):
    with engine.begin() as connection:
        row = connection.execute(select(problems).where(problems.c.id == problem_id)).mappings().first()

    if row is None:
        raise AppError("PROBLEM_NOT_FOUND", "Problem not found.")
    return row


def _problem_original_code(problem, repo_path: Path) -> str:
    if problem["grading_method"] == "llm" and problem["original_code"]:
        return problem["original_code"]

    source_file = repo_path / problem["source_path"]
    if not source_file.exists():
        return problem["starter_code"]

    source = source_file.read_text(encoding="utf-8")
    extracted = _extract_function_source(source, problem["target_symbol"])
    return extracted or problem["starter_code"]


def _extract_function_source(source: str, target_symbol: str) -> str | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_symbol:
            lines = source.splitlines(keepends=True)
            return "".join(lines[node.lineno - 1 : node.end_lineno])
    return None


def _build_prompt(level: int, target_symbol: str, original_code: str) -> str:
    if level == 1:
        return f"""아래 함수에 대해 개념적인 힌트를 마크다운으로 작성해주세요.

함수명: {target_symbol}
코드 참고:
{original_code}

아래 형식을 따르세요:
### 💡 개념 힌트
[이 함수의 역할을 2~3문장으로]

- 핵심 개념 1
- 핵심 개념 2

정답 코드는 절대 포함하지 마세요."""

    if level == 2:
        return f"""아래 함수에 대해 입출력 힌트를 마크다운으로 작성해주세요.

함수명: {target_symbol}
코드 참고:
{original_code}

아래 형식을 따르세요:
### 📋 입출력 힌트

**입력**
- 각 파라미터 설명

**출력**
- 반환값 설명

**예시**
- 입력 → 출력 예시 1~2개

정답 코드는 절대 포함하지 마세요."""

    return f"""아래 함수의 구현 방향을 마크다운으로 작성해주세요.

함수명: {target_symbol}
코드 참고:
{original_code}

아래 형식을 따르세요:
### 🔧 구현 방향

1. 첫 번째 단계
2. 두 번째 단계
3. ...

> 💡 핵심 팁 한 줄

구체적인 코드는 절대 포함하지 마세요.
구현 방향만 제시하세요."""
