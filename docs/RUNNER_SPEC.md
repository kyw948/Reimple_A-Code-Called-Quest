# RUNNER_SPEC.md

## 실행 방식
pytest를 subprocess로 실행한다.

## Timeout 정책
용도별로 분리한다.

```txt
SYNTAX_TIMEOUT_SECONDS=5
RUNNER_TEST_TIMEOUT_SECONDS=10
RUNNER_SUBMIT_TIMEOUT_SECONDS=30
```

- syntax/import check: 최대 5초
- 단일 테스트 파일 실행: 최대 10초
- 제출 전체 채점: 최대 30초

## 실행 환경 구성
채점 시 아래 순서로 임시 환경을 만든다.

1. 원본 repo 전체를 임시 디렉토리에 복사한다
2. 제출된 코드를 해당 source_path 위치에 덮어쓴다
3. 임시 디렉토리를 cwd로 pytest를 실행한다
4. 채점 완료 후 임시 디렉토리를 삭제한다

예:
```txt
임시 디렉토리: /tmp/runner_{submission_id}/
제출 파일 위치: /tmp/runner_{submission_id}/src/math_utils.py
실행: cd /tmp/runner_{submission_id} && pytest tests/test_math_utils.py -q
```

PYTHONPATH는 별도 설정하지 않는다.
원본 repo의 import 구조를 그대로 유지하므로
pyproject.toml이나 setup.py가 있으면 원본과 동일하게 동작한다.

## 임시 디렉토리 정리
- 성공/실패 모두 finally에서 삭제한다.
- 삭제 실패 시 로그만 남기고 진행한다.
- 복사 시 제외 경로(.git, __pycache__, .venv 등)는 복사하지 않는다.

## 채점 성공 조건
- syntax error 없음
- import error 없음
- 매칭된 pytest 테스트 통과
- timeout 없음
- return code 0

## 실패 조건
- syntax error
- import error
- pytest failure
- timeout
- 테스트 파일 없음

## pytest 실행 대상
MVP-0은 매칭된 테스트 파일만 실행한다.

예:
```bash
pytest tests/test_math_utils.py -q --tb=short --json-report
```

## 저장 조건
채점 성공 시에만 practice root에 파일을 저장한다.
