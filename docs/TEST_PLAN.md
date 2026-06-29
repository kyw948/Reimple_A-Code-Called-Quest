# TEST_PLAN.md

## 샘플 repo 구조
테스트용 샘플은 아래 구조로 통일한다.

```txt
samples/python_basic/
  README.md
  pyproject.toml
  src/
    math_utils.py
    string_utils.py
  tests/
    test_math_utils.py
    test_string_utils.py
```

## 샘플 함수 목록
math_utils.py: add_numbers, multiply_numbers, clamp, factorial, average
string_utils.py: reverse_string, count_vowels, truncate, is_palindrome

모든 함수는 함수 후보 기준을 만족한다 (body 3줄 이상, return 존재, 파라미터 1~5개).

## Repo 분석 테스트
- 존재하지 않는 repo path → 400 `INVALID_REPO_PATH`
- 정상 repo path → 파일 트리 반환 (FileTreeNode 구조)
- 확장자 통계 반환 (`.py`, `.md`, `.toml`)
- 제외 경로(.git, __pycache__ 등) 미포함

## 프로젝트 테스트
- 프로젝트 생성 → project_id 반환
- 프로젝트 목록 조회 → 생성된 프로젝트 포함
- 프로젝트 상세 조회 → file_counts 포함
- target_extensions JSON 배열 저장 확인

## 파일 복사 테스트 (setup)
- setup 호출 시 files 테이블에 모든 파일 등록
- `.py` 대상 파일: is_target=1, status="pending"
- 비대상 파일: is_target=0, status="skipped"
- 비대상 파일만 practice_root에 복사
- `.py` 대상 파일은 초기 복사하지 않음
- 트리 구조 유지

## 문제 생성 테스트
- `src/math_utils.py` → `tests/test_math_utils.py` 매칭
- `src/string_utils.py` → `tests/test_string_utils.py` 매칭
- 테스트 없는 파일은 `TEST_NOT_FOUND` (422)
- 원본 테스트 실패 시 `BASELINE_TEST_FAILED` (422)
- body 2줄 이하 함수 제외
- `__init__`, `@property`, dunder method 제외
- 알 수 없는 데코레이터가 있는 함수 제외
- 파일 1개당 문제 1개 생성
- prompt 생성 확인 (시그니처 + docstring 포함)
- starter_code에 `raise NotImplementedError` 포함 확인

## Runner 테스트
- 정답 코드 제출 → passed=true, saved_path 반환
- 오답 코드 제출 → passed=false, saved_path=null
- syntax error → failed, stderr에 에러 내용
- timeout → `RUNNER_TIMEOUT` (408)
- 임시 디렉토리가 채점 후 삭제됨 확인
- overwrite=false이고 파일 존재 시 → saved_path=null
- overwrite=true이고 파일 존재 시 → 덮어쓰기, saved_path 반환

## UI 테스트
- SetupPage: repo path 입력 → 분석 버튼 → AnalyzePage 이동
- AnalyzePage: 파일 트리 표시, 확장자 통계 표시, 세팅 버튼 → PracticePage 이동
- PracticePage: 문제 목록 표시, 에디터에 starter_code 로드
- 제출 결과 표시 (passed/failed, stdout/stderr)
