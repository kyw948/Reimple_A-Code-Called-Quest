# CLAUDE.md

## Project Context
로컬 또는 GitHub의 코드 repository를 코딩 연습 문제로 변환하는 localhost 앱이다.

## 현재 포커스
AI 연구 논문의 Python 기반 repository에 집중한다.
범용 코딩 연습 도구가 아니라, AI 논문 코드를 구현하면서 학습하는 도구이다.
논문의 모델 구조를 이해하고 단계별로 구현해보는 것이 핵심 학습 경험이다.

## 주요 결정사항
- Localhost browser app (FastAPI + React)
- SQLite DB
- Gemini LLM (파일 분석, 채점, 힌트)
- Python AI repo 우선 (다른 언어는 LLM 채점으로 지원)
- 간단한 함수는 pytest, 복잡한 함수나 외부 패키지 필요 시 LLM 채점
- PaperBench 영감의 계층적 문제 트리 구조 (가중치 + 의존 관계 + 해금)
- Paper2Code 영감의 프로젝트 수준 분석 (전체 이해 → 구조 분석 → 의존성 → 함수별 분석)

## 분석 방법론 (Paper2Code 역방향 적용)
기존 코드를 이해하는 4단계 분석:
1. Project Understanding: 프로젝트가 뭔지 전체 파악 (README, 구조)
2. Architecture Recovery: 파일 간 관계, 모듈 분류 (model/data/training/utils)
3. Dependency Graph: 함수/클래스 호출 관계, 구현 순서 결정
4. Function Analysis: 각 함수의 역할, 입출력, 프로그램 내 위치

## 문제 구조 (PaperBench 영감)
- 문제는 트리 구조 (repo → 모듈 → 파일 → 함수)
- 각 노드에 가중치 (중요도)
- 의존 관계에 따른 해금 (PatchEmbed 완료 → TransformerBlock 해금)
- 부분 점수 지원

## API Convention
모든 API 경로는 `/api`로 시작한다.

## 환경변수
- DATABASE_URL, GEMINI_API_KEY
- FRONTEND_ORIGIN=http://localhost:5173
- GITHUB_CLONE_BASE_PATH=~/.codepractice/repos

## 참고 논문/프로젝트
- Paper2Code (PaperCoder, ICLR 2026): 논문 분석 방법론 참고
- PaperBench (OpenAI): 계층적 rubric/문제 트리 구조 참고
