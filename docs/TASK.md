# TASK.md

## Phase 1. Backend 기초
- T001: FastAPI 프로젝트 생성
- T002: SQLite 연결 구성
- T003: CORS 설정
- T004: 공통 에러 응답 구현

## Phase 2. Repo 분석
- T010: `POST /api/repos/analyze` 구현
- T011: 파일 트리 구조 생성
- T012: 확장자 통계 생성
- T013: 제외 경로 처리

## Phase 3. 프로젝트 관리
- T020: `POST /api/projects` 구현
- T021: `GET /api/projects` 구현
- T022: `GET /api/projects/{project_id}` 구현
- T023: `POST /api/projects/{project_id}/setup` 구현 (files 테이블 등록 포함)

## Phase 4. 문제 생성
- T030: Python AST 함수 분석
- T031: 테스트 파일 매칭
- T032: 문제 후보 점수화
- T033: `POST /api/projects/{project_id}/problems/generate` 구현
- T034: `GET /api/projects/{project_id}/problems` 구현
- T035: `GET /api/problems/{problem_id}` 구현
- T036: baseline 테스트 검증 (원본 repo 테스트 통과 확인)
- T037: prompt 자동 생성 (시그니처 + docstring 기반)

## Phase 5. Runner
- T040: 임시 디렉토리 기반 실행 환경 구성
- T041: pytest subprocess 실행
- T042: timeout 적용 (3단계 분리)
- T043: stdout/stderr 수집
- T044: 제출 성공 시 파일 저장 (overwrite 규칙 적용)
- T045: 임시 디렉토리 정리

## Phase 6. Frontend
- T050: SetupPage 구현
- T051: AnalyzePage 구현 (setup + 일괄 문제 생성 흐름)
- T052: PracticePage 3열 레이아웃 구현
- T053: Monaco Editor 연동
- T054: 제출 결과 표시

## Phase 7. 샘플 repo
- T060: `samples/python_basic` 샘플 코드 작성
- T061: 샘플 테스트 파일 작성
- T062: 샘플 repo 기반 통합 테스트
