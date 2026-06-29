# SECURITY.md

## 기본 원칙
MVP-0은 로컬 실행 앱이지만 사용자의 코드를 실행하므로 최소 제한을 둔다.

## Timeout 정책
```txt
SYNTAX_TIMEOUT_SECONDS=5
RUNNER_TEST_TIMEOUT_SECONDS=10
RUNNER_SUBMIT_TIMEOUT_SECONDS=30
```

## 실행 제한
- subprocess 실행 시 timeout 필수
- 채점은 임시 디렉토리(/tmp/runner_{submission_id}/)에서 수행한다
- 임시 디렉토리는 채점 완료 후 반드시 삭제한다
- 원본 repo는 read-only로 취급한다
- 결과 저장은 채점 통과 후에만 practice root에 수행한다

## 위험 기능
MVP-0에서는 정적 차단을 강제하지 않는다.
단, 이후 버전에서 아래 사용을 감지/경고한다.

```txt
os.remove
shutil.rmtree
subprocess
socket
requests
open(..., 'w')
```

## 네트워크
MVP-0에서는 OS 레벨 네트워크 차단은 하지 않는다.
Docker sandbox 도입 시 차단한다.

## 에러 처리
실행 실패 시 stdout/stderr는 사용자에게 보여주되, 내부 절대경로 노출은 가능한 줄인다.
