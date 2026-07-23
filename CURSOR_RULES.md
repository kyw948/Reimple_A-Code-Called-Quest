# CURSOR_RULES.md

## 프로젝트 개요
AI 연구 논문의 Python repository를 코딩 연습 문제로 변환하는 localhost 앱이다.
범용 코딩 연습 도구가 아니라, AI 논문 코드를 단계별로 구현하면서 학습하는 도구이다.

## 현재 포커스
- AI 연구 논문의 Python 기반 GitHub repository
- 논문의 모델 구조를 이해하고 단계별로 구현
- PaperBench 영감의 계층적 문제 트리 (가중치 + 의존 관계 + 해금)
- Paper2Code 영감의 프로젝트 수준 분석 (전체 이해 → 구조 → 의존성 → 함수별)

## 기술 스택
- Backend: FastAPI, SQLite, python-dotenv, google-genai (Gemini 2.5 Flash), pytest
- Frontend: React, TypeScript, Vite, Zustand, Monaco Editor, react-markdown
- LLM: Gemini (파일 분석, 문제 생성, 채점, 힌트)

## 분석 파이프라인 (4단계)
1. Project Understanding: 프로젝트 전체 이해 (README, 구조) — LLM 1회
2. Architecture Recovery: 파일 관계, 모듈 분류 — LLM 1회
3. Dependency Graph: 함수 호출 관계, 구현 순서 — AST/코드 분석
4. Function Analysis: 함수별 역할, 입출력, 맥락 — LLM 배치

## 문제 구조
- 트리 구조: repo → 모듈 → 파일 → 함수
- 의존 관계 기반 해금 (PatchEmbed 완료 → TransformerBlock 해금)
- 가중치로 중요도 표현
- 부분 점수 지원

## 채점 전략
- Python + pytest 있음 → pytest 실행
- Python + pytest 없음 + 외부 패키지 불필요 → LLM 테스트 케이스 + 실행
- 외부 패키지 필요 (torch 등) → LLM 채점 (코드 비교)

## 구현 원칙
- 원본 repo는 수정하지 않는다
- API prefix는 `/api`
- 문제 생성 시 분석 결과를 DB에 저장하여 재사용
- LLM 응답은 JSON schema validation + 실패 시 1회 재시도
- assess 결과는 DB 캐싱 (같은 파일 재분석 방지)

## 금지
- Docker sandbox (아직)
- Tauri/Electron (아직)
- 로그인/계정

## 참고 문서
- docs/PROBLEM_GENERATION.md: 분석 파이프라인 + 문제 트리 상세
- docs/MVP-1.md, docs/MVP-2.md: 기능 명세
- docs/API.md: API 명세


## 필수 규칙

### 파일 인코딩
- 모든 파일은 UTF-8로 읽고 써야 한다
- Python에서 파일 읽기/쓰기 시 반드시 encoding="utf-8" 명시
  - open(path, "r", encoding="utf-8")
  - open(path, "w", encoding="utf-8")
  - Path.read_text(encoding="utf-8")
  - Path.write_text(content, encoding="utf-8")
- 한글이 포함된 문자열이 깨지면 안 된다
- 파일 수정 시 기존 한글 내용을 ??로 바꾸지 마라