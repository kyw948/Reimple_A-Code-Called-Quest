# FIRST_TASKS.md

## First milestone
로컬 repo path를 입력하면 파일 트리와 확장자 통계를 반환한다.

## Backend
- `POST /api/repos/analyze`
- repo_path 유효성 검사
- 제외 경로 처리
- FileTreeNode 생성
- extension_stats 생성
- 공통 에러 응답 적용

## Frontend
- SetupPage 생성
- repo path 입력창
- analyze 버튼
- file tree 표시
- extension stats 표시

## 완료 조건
- `samples/python_basic` 분석 성공
- `.py`, `.md`, `.toml` 확장자 통계 표시
- 파일 트리에서 `src/math_utils.py` 표시
- 잘못된 경로 입력 시 에러 표시
