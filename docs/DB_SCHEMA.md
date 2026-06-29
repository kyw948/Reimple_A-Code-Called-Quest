# DB_SCHEMA.md

## ID 규칙
모든 ID는 backend에서 생성하는 UUID v4 문자열이다.

## projects
```sql
CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  repo_path TEXT NOT NULL,
  practice_root_path TEXT NOT NULL,
  target_extensions TEXT NOT NULL, -- JSON array string, e.g. [".py"]
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

## files
```sql
CREATE TABLE files (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  source_path TEXT NOT NULL,
  extension TEXT NOT NULL,
  is_target INTEGER NOT NULL,
  status TEXT NOT NULL, -- pending, problem_created, passed, skipped
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

## problems
```sql
CREATE TABLE problems (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  file_id TEXT NOT NULL,
  source_path TEXT NOT NULL,
  target_symbol TEXT NOT NULL,
  problem_type TEXT NOT NULL, -- function_blank
  prompt TEXT NOT NULL,
  starter_code TEXT NOT NULL,
  test_path TEXT,
  status TEXT NOT NULL, -- active, passed, failed
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

관계:
- file 1개는 problem 여러 개를 가질 수 있다.
- MVP-0에서는 file 1개당 problem 1개만 생성한다.

## submissions
```sql
CREATE TABLE submissions (
  id TEXT PRIMARY KEY,
  problem_id TEXT NOT NULL,
  code TEXT NOT NULL,
  passed INTEGER NOT NULL,
  stdout TEXT,
  stderr TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL
);
```

## test_results
```sql
CREATE TABLE test_results (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL,
  test_nodeid TEXT NOT NULL,
  passed INTEGER NOT NULL,
  message TEXT,
  duration_ms INTEGER
);
```

역할:
- `submissions`: 제출 1회의 전체 결과
- `test_results`: pytest 개별 test case 단위 결과
- MVP-0에서는 `test_results` 저장은 선택이며, stdout/stderr만 저장해도 된다.
