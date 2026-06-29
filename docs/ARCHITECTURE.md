# ARCHITECTURE.md

## MVP-0 실행 방식
데스크톱 shell은 아직 사용하지 않는다.

- Frontend: Vite + React + Monaco Editor
- Backend: FastAPI
- DB: SQLite
- Runner: Python subprocess + pytest
- 실행: 브라우저에서 `http://localhost:5173` 접속

## 구조
```txt
app/
  frontend/
  backend/
  samples/
  docs/
```

## Backend 역할
- repo 분석
- 프로젝트 생성/조회
- 파일 복사 및 files 테이블 등록
- 문제 생성 (AST 분석 + 테스트 매칭 + baseline 검증)
- 임시 디렉토리 기반 pytest 실행
- 결과 저장

## Frontend 역할
- 설정 화면 (repo path, practice root path 입력)
- 분석 결과 화면 (파일 트리, 확장자 통계)
- 연습 화면 (3열: 파일 목록 / 문제+에디터 / 채점 결과)
- 코드 에디터 (Monaco Editor)
- 채점 결과 표시

## 흐름
```txt
SetupPage
  → POST /api/repos/analyze
  → POST /api/projects

AnalyzePage
  → GET /api/projects/{project_id}
  → POST /api/projects/{project_id}/setup
  → POST /api/projects/{project_id}/problems/generate (대상 파일별 반복)

PracticePage
  → GET /api/projects/{project_id}/problems
  → GET /api/problems/{problem_id}
  → POST /api/problems/{problem_id}/submit
```

## CORS
개발 환경에서는 frontend `5173`에서 backend `8000` 호출을 허용한다.
`FRONTEND_ORIGIN` 환경변수로 설정한다.
