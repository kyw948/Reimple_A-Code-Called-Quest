# IMPLEMENTATION_PLAN.md

## 1단계
Backend 뼈대를 만든다.

- FastAPI 생성
- SQLite 연결
- CORS 설정
- 공통 에러 응답

## 2단계
Repo 분석 기능을 만든다.

- `POST /api/repos/analyze`
- file tree (FileTreeNode 구조)
- extension stats

## 3단계
프로젝트 관리 기능을 만든다.

- `POST /api/projects`
- `GET /api/projects`
- `GET /api/projects/{project_id}` (file_counts 포함)
- `POST /api/projects/{project_id}/setup` (files 테이블 등록 + 비대상 파일 복사)

## 4단계
문제 생성 기능을 만든다.

- Python AST 분석 (함수 후보 필터링 + 점수화)
- 테스트 파일 매칭 (5단계 탐색)
- baseline 테스트 검증
- prompt 자동 생성 (시그니처 + docstring)
- starter code 생성
- `POST /api/projects/{project_id}/problems/generate`
- `GET /api/projects/{project_id}/problems`
- `GET /api/problems/{problem_id}`

## 5단계
채점 기능을 만든다.

- 임시 디렉토리에 원본 repo 복사
- 제출 코드를 해당 source_path에 덮어쓰기
- pytest subprocess 실행 (3단계 timeout)
- stdout/stderr 수집
- 통과 시 practice root에 저장 (overwrite 규칙 적용)
- 임시 디렉토리 정리

## 6단계
Frontend를 붙인다.

- React Router (SetupPage → AnalyzePage → PracticePage)
- Zustand 상태 관리
- SetupPage: repo path 입력 + 분석
- AnalyzePage: 파일 트리 + 확장자 통계 + setup + 일괄 문제 생성
- PracticePage: 3열 레이아웃 + Monaco Editor + 제출/채점

## 7단계
샘플 repo와 통합 테스트

- `samples/python_basic` 샘플 코드 작성
- 전체 흐름 통합 테스트
