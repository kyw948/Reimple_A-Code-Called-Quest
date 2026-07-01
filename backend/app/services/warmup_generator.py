import json
import random
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.db.database import engine, projects
from app.services import llm_client


WARMUP_SYSTEM_INSTRUCTION = """너는 AI 논문 코딩 학습 도우미이다.
프로젝트 개요를 보고 학습자가 본 프로젝트를 시작하기 전에
풀어볼 수 있는 간단한 몸풀기 퀴즈를 만들어라.
반드시 JSON 형식으로만 응답하라."""


class WarmupQuestion(BaseModel):
    id: int
    question: str
    options: list[str]
    answer: int
    explanation: str


class WarmupResponse(BaseModel):
    questions: list[WarmupQuestion]


def generate_warmup(project_id: str) -> WarmupResponse:
    summary = _project_summary(project_id)
    parsed = None
    try:
        parsed = llm_client.call_gemini_with_validation(
            _build_prompt(summary),
            WARMUP_SYSTEM_INSTRUCTION,
            {"warmup_questions": list},
            max_retries=1,
        )
    except Exception:
        parsed = None

    questions = _normalize_questions(parsed)
    if not questions:
        questions = _fallback_questions(summary)
    return WarmupResponse(questions=questions)


def _build_prompt(summary: dict[str, Any]) -> str:
    return f"""아래 프로젝트에 대한 몸풀기 퀴즈 3~5개를 만들어주세요.
코딩 문제가 아니라 개념 확인 퀴즈입니다.

프로젝트: {summary.get("project_summary") or "프로젝트 개요 없음"}
도메인: {summary.get("domain") or "other"}
프레임워크: {summary.get("framework") or "other"}
핵심 기여: {summary.get("main_contribution") or "없음"}

JSON으로 응답:
{{
  "warmup_questions": [
    {{
      "question": "ViTPose 모델의 backbone으로 사용되는 아키텍처는?",
      "options": ["ResNet", "Vision Transformer", "EfficientNet", "MobileNet"],
      "answer": 1,
      "explanation": "ViTPose는 Vision Transformer(ViT)를 backbone으로 사용합니다."
    }}
  ]
}}"""


def _normalize_questions(parsed: Any) -> list[WarmupQuestion]:
    if not isinstance(parsed, dict):
        return []
    raw_questions = parsed.get("warmup_questions")
    if not isinstance(raw_questions, list):
        return []

    questions: list[WarmupQuestion] = []
    for index, item in enumerate(raw_questions[:5]):
        if not isinstance(item, dict):
            continue
        options = item.get("options")
        answer = item.get("answer")
        if not isinstance(options, list) or len(options) < 2 or not isinstance(answer, int):
            continue
        if answer < 0 or answer >= len(options):
            continue
        question = str(item.get("question") or "").strip()
        explanation = str(item.get("explanation") or "").strip()
        if not question or not explanation:
            continue
        shuffled_options, shuffled_answer = _shuffle_answer([str(option) for option in options], answer)
        questions.append(
            WarmupQuestion(
                id=len(questions),
                question=question,
                options=shuffled_options,
                answer=shuffled_answer,
                explanation=explanation,
            )
        )
    return questions


def _fallback_questions(summary: dict[str, Any]) -> list[WarmupQuestion]:
    framework = str(summary.get("framework") or "other")
    domain = str(summary.get("domain") or "other")
    contribution = str(summary.get("main_contribution") or "프로젝트의 핵심 아이디어")
    questions = [
        WarmupQuestion(
            id=0,
            question="이 프로젝트를 풀기 전에 가장 먼저 파악하면 좋은 정보는 무엇인가요?",
            options=["파일 이름의 길이", "프로젝트의 도메인과 핵심 모듈", "README의 글자 수", "라이선스 연도"],
            answer=1,
            explanation="코딩 연습을 시작하기 전에는 도메인, 프레임워크, 핵심 모듈을 먼저 잡는 것이 좋습니다.",
        ),
        WarmupQuestion(
            id=1,
            question=f"이 프로젝트의 분석된 프레임워크는 무엇인가요?",
            options=["pytorch", "tensorflow", "jax", framework],
            answer=3,
            explanation=f"프로젝트 분석 결과 프레임워크는 {framework}로 요약되었습니다.",
        ),
        WarmupQuestion(
            id=2,
            question="문제를 풀 때 함수의 역할을 이해하는 데 가장 도움이 되는 맥락은 무엇인가요?",
            options=["import와 호출 관계", "빈 줄 개수", "파일 크기만", "폴더 아이콘 색상"],
            answer=0,
            explanation=f"이 프로젝트는 {domain} 영역이며, 함수의 import와 호출 관계를 보면 {contribution}와 연결된 역할을 이해하기 쉽습니다.",
        ),
    ]
    return [_shuffle_warmup_question(question) for question in questions]


def _shuffle_answer(options: list[str], answer: int) -> tuple[list[str], int]:
    correct_answer = options[answer]
    shuffled = options[:]
    random.shuffle(shuffled)
    return shuffled, shuffled.index(correct_answer)


def _shuffle_warmup_question(question: WarmupQuestion) -> WarmupQuestion:
    options, answer = _shuffle_answer(question.options, question.answer)
    return WarmupQuestion(
        id=question.id,
        question=question.question,
        options=options,
        answer=answer,
        explanation=question.explanation,
    )


def _project_summary(project_id: str) -> dict[str, Any]:
    with engine.begin() as connection:
        row = connection.execute(select(projects.c.project_summary).where(projects.c.id == project_id)).mappings().first()
    if row is None or not row["project_summary"]:
        return {}
    try:
        parsed = json.loads(row["project_summary"])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
