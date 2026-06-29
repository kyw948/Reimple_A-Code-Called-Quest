# API.md

## 공통
API prefix는 `/api`로 통일한다.

## 공통 에러 응답
```json
{
  "error_code": "INVALID_REPO_PATH",
  "message": "Repo path does not exist."
}
```

## HTTP status 매핑
- `INVALID_REPO_PATH`: 400
- `PROJECT_NOT_FOUND`: 404
- `PROBLEM_NOT_FOUND`: 404
- `TEST_NOT_FOUND`: 422
- `BASELINE_TEST_FAILED`: 422
- `RUNNER_TIMEOUT`: 408
- `INTERNAL_ERROR`: 500

## FileTreeNode
디렉토리 노드:
```json
{
  "name": "src",
  "path": "src",
  "type": "directory",
  "children": []
}
```

파일 노드:
```json
{
  "name": "math_utils.py",
  "path": "src/math_utils.py",
  "type": "file",
  "extension": ".py",
  "size_bytes": 1200
}
```

## POST /api/repos/analyze
Repo를 분석한다.

Request:
```json
{
  "repo_path": "/local/repo"
}
```

Response:
```json
{
  "repo_path": "/local/repo",
  "file_tree": [],
  "extension_stats": {
    ".py": 12,
    ".md": 2
  }
}
```

## POST /api/projects
프로젝트를 생성한다.

Request:
```json
{
  "repo_path": "/local/repo",
  "practice_root_path": "/local/practice",
  "target_extensions": [".py"]
}
```

Response:
```json
{
  "project_id": "uuid-v4"
}
```

## GET /api/projects
프로젝트 목록을 조회한다.

Response:
```json
{
  "projects": [
    {
      "id": "uuid-v4",
      "repo_path": "/local/repo",
      "practice_root_path": "/local/practice",
      "target_extensions": [".py"],
      "created_at": "2025-01-01T00:00:00Z",
      "updated_at": "2025-01-01T00:00:00Z"
    }
  ]
}
```

## GET /api/projects/{project_id}
프로젝트 상세를 조회한다.

Response:
```json
{
  "id": "uuid-v4",
  "repo_path": "/local/repo",
  "practice_root_path": "/local/practice",
  "target_extensions": [".py"],
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z",
  "file_counts": {
    "total": 22,
    "target": 12,
    "passed": 3,
    "pending": 9
  }
}
```

## POST /api/projects/{project_id}/setup
비대상 파일을 practice root에 복사하고, 모든 파일을 files 테이블에 등록한다.

동작:
1. repo 내 모든 파일을 files 테이블에 등록한다.
   - 대상 확장자 파일: `is_target=1`, `status="pending"`
   - 비대상 파일: `is_target=0`, `status="skipped"`
2. 비대상 파일만 practice_root에 복사한다.
3. 대상 파일은 복사하지 않는다 (채점 통과 후 저장).

Response:
```json
{
  "copied_files": 10,
  "skipped_files": 12
}
```

## POST /api/projects/{project_id}/problems/generate
문제를 생성한다.

Request:
```json
{
  "source_path": "src/math_utils.py"
}
```

Response (성공):
```json
{
  "problem_id": "uuid-v4",
  "file_id": "uuid-v4",
  "source_path": "src/math_utils.py",
  "target_symbol": "add_numbers",
  "test_path": "tests/test_math_utils.py"
}
```

MVP-0은 파일 1개당 문제 1개만 생성한다.

에러 케이스:
- 테스트 파일 없음 → `TEST_NOT_FOUND` (422)
- 원본 테스트 실패 → `BASELINE_TEST_FAILED` (422)
- 후보 함수 없음 → `PROBLEM_NOT_FOUND` (404)

## GET /api/projects/{project_id}/problems
문제 목록을 조회한다.

Response:
```json
{
  "problems": [
    {
      "id": "uuid-v4",
      "source_path": "src/math_utils.py",
      "target_symbol": "add_numbers",
      "status": "active"
    }
  ]
}
```

## GET /api/problems/{problem_id}
문제 상세를 조회한다.

Response:
```json
{
  "id": "uuid-v4",
  "project_id": "uuid-v4",
  "file_id": "uuid-v4",
  "source_path": "src/math_utils.py",
  "target_symbol": "add_numbers",
  "problem_type": "function_blank",
  "prompt": "아래 함수를 구현하세요.\nadd_numbers(a: int, b: int) -> int\n두 정수를 더한 값을 반환합니다.",
  "starter_code": "def add_numbers(a: int, b: int) -> int:\n    # TODO: implement this function\n    raise NotImplementedError",
  "test_path": "tests/test_math_utils.py",
  "status": "active",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

## POST /api/problems/{problem_id}/submit
코드를 제출하고 채점한다.

Request:
```json
{
  "code": "def add_numbers(a, b):\n    return a + b",
  "overwrite": false
}
```

Response:
```json
{
  "passed": true,
  "stdout": "",
  "stderr": "",
  "duration_ms": 340,
  "saved_path": "src/math_utils.py"
}
```

## saved_path 규칙
- `saved_path`는 practice_root 기준 상대경로이다.
- 예: `"src/math_utils.py"`

## overwrite 동작
- `passed=true`이고 해당 경로에 파일이 없으면: 저장, `saved_path` 반환
- `passed=true`이고 파일이 있고 `overwrite=false`: 저장하지 않음, `saved_path=null`
- `passed=true`이고 파일이 있고 `overwrite=true`: 덮어쓰기, `saved_path` 반환
- `passed=false`: 저장하지 않음, `saved_path=null`
