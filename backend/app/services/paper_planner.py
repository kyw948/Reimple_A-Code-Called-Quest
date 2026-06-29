import json
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.core.errors import AppError
from app.db.database import engine, projects
from app.services import llm_client


PAPER_OVERALL_PLAN_SYSTEM = """너는 AI 논문을 코드로 구현하는 전문가이다.
논문을 읽고 구현해야 할 핵심 컴포넌트를 추출하라.
반드시 JSON 형식으로만 응답하라."""

PAPER_ARCHITECTURE_SYSTEM = """너는 AI 논문 구현을 위한 Python 프로젝트 설계자이다.
구현 계획을 바탕으로 PyTorch 기반 파일 구조와 클래스 구조를 설계하라.
반드시 JSON 형식으로만 응답하라."""

PAPER_LOGIC_SYSTEM = """너는 AI 논문 구현을 위한 상세 명세 작성자이다.
파일 구조를 바탕으로 구현 순서와 클래스/함수별 상세 로직을 작성하라.
반드시 JSON 형식으로만 응답하라."""


class PaperPlanRequest(BaseModel):
    force: bool = False


class PaperPlanResponse(BaseModel):
    status: str
    overall_plan: dict[str, Any] = Field(default_factory=dict)
    architecture: dict[str, Any] = Field(default_factory=dict)
    logic_design: dict[str, Any] = Field(default_factory=dict)


def plan_paper_project(project_id: str, payload: PaperPlanRequest | None = None) -> PaperPlanResponse:
    payload = payload or PaperPlanRequest()
    project = _get_project(project_id)
    if not project["paper_source"]:
        raise AppError("INVALID_PROJECT_MODE", "논문 기반 프로젝트만 구조 설계를 실행할 수 있습니다.")

    cached_overall = _loads(project["project_summary"])
    cached_architecture = _loads(project["architecture"])
    cached_logic = _loads(project["dependency_graph"])
    if (
        not payload.force
        and project["analysis_status"] in {"planned", "completed"}
        and cached_overall
        and cached_architecture
        and cached_logic
    ):
        return PaperPlanResponse(
            status="planned",
            overall_plan=cached_overall,
            architecture=cached_architecture,
            logic_design=cached_logic,
        )

    _update_project(project_id, analysis_status="planning")

    overall_plan = _run_overall_plan(project)
    _update_project(project_id, project_summary=json.dumps(overall_plan, ensure_ascii=False))

    architecture = _run_architecture_design(project, overall_plan)
    _update_project(project_id, architecture=json.dumps(architecture, ensure_ascii=False))

    logic_design = _run_logic_design(project, overall_plan, architecture)
    _update_project(
        project_id,
        dependency_graph=json.dumps(logic_design, ensure_ascii=False),
        analysis_status="planned",
    )

    return PaperPlanResponse(
        status="planned",
        overall_plan=overall_plan,
        architecture=architecture,
        logic_design=logic_design,
    )


def _run_overall_plan(project) -> dict[str, Any]:
    prompt = f"""아래 논문을 읽고 Python으로 구현하기 위해 필요한 핵심 컴포넌트를 추출해주세요.

논문 제목: {project["paper_title"] or ""}
논문 Abstract: {project["paper_abstract"] or ""}
논문 본문:
{_truncate(project["paper_content"] or "", 50_000)}

JSON으로 응답:
{{
  "summary": "논문 핵심 내용 3~5문장",
  "domain": "computer_vision | nlp | speech | reinforcement_learning | generative | other",
  "framework": "pytorch",
  "components": [
    {{
      "name": "컴포넌트 이름",
      "description": "역할 설명",
      "category": "model_component | head | loss | training | data | config | other",
      "importance": "core | supporting"
    }}
  ],
  "key_algorithms": ["핵심 알고리즘"],
  "required_libraries": ["torch", "numpy"]
}}"""
    return _call_planning_step(
        prompt,
        PAPER_OVERALL_PLAN_SYSTEM,
        {
            "summary": str,
            "domain": str,
            "framework": str,
            "components": list,
            "key_algorithms": list,
            "required_libraries": list,
        },
        "Overall Plan",
    )


def _run_architecture_design(project, overall_plan: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""아래 논문 구현 계획을 기반으로 Python 프로젝트 파일 구조를 설계해주세요.

논문: {project["paper_title"] or ""}
구현 계획:
{json.dumps(overall_plan, ensure_ascii=False, indent=2)}

규칙:
- PyTorch 기반
- models/ 디렉토리에 모델 컴포넌트 배치
- 파일 하나당 1~2개 클래스
- 학습, 평가 스크립트는 루트에 배치
- config.py에 하이퍼파라미터 배치

JSON으로 응답:
{{
  "files": [
    {{
      "path": "models/attention.py",
      "description": "파일 역할",
      "classes": ["ClassName"],
      "functions": [],
      "depends_on": []
    }}
  ]
}}"""
    return _call_planning_step(
        prompt,
        PAPER_ARCHITECTURE_SYSTEM,
        {"files": list},
        "Architecture Design",
    )


def _run_logic_design(project, overall_plan: dict[str, Any], architecture: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""아래 파일 구조를 기반으로 구현 순서와 각 클래스/함수의 상세 명세를 작성해주세요.

논문: {project["paper_title"] or ""}
구현 계획:
{json.dumps(overall_plan, ensure_ascii=False, indent=2)}

파일 구조:
{json.dumps(architecture, ensure_ascii=False, indent=2)}

규칙:
- 의존성이 없는 파일부터 구현 순서로 정렬
- 각 클래스의 __init__ 파라미터와 forward 입출력을 명확히 작성
- 논문 수식/알고리즘을 코드 수준 설명으로 작성

JSON으로 응답:
{{
  "implementation_order": ["config.py", "models/attention.py"],
  "specifications": [
    {{
      "file": "models/attention.py",
      "class": "MultiHeadAttention",
      "methods": {{
        "__init__": {{
          "params": {{"d_model": "int"}},
          "description": "초기화 설명"
        }},
        "forward": {{
          "params": {{"x": "Tensor"}},
          "returns": "Tensor",
          "algorithm": "구현 단계",
          "paper_reference": "Section 3"
        }}
      }}
    }}
  ]
}}"""
    return _call_planning_step(
        prompt,
        PAPER_LOGIC_SYSTEM,
        {"implementation_order": list, "specifications": list},
        "Logic Design",
    )


def _call_planning_step(prompt: str, system_instruction: str, required_fields: dict, step_name: str) -> dict[str, Any]:
    parsed = llm_client.call_gemini_with_validation(
        prompt,
        system_instruction,
        required_fields,
        max_retries=1,
    )
    if isinstance(parsed, dict):
        return parsed

    last_error = llm_client.get_last_validation_error()
    if isinstance(last_error, AppError):
        raise last_error
    raise AppError("PAPER_PLAN_FAILED", f"{step_name} 생성에 실패했습니다. LLM 응답 형식을 확인하세요.")


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


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n\n[... 이후 내용 생략 ...]"
