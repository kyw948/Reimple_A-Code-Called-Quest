# FILE_RULES.md

## 기본 원칙
- 원본 repo는 절대 수정하지 않는다.
- 모든 생성/수정은 practice root 아래에서만 수행한다.
- 원본 repo의 상대 경로를 그대로 유지한다.

## setup 동작
`POST /api/projects/{project_id}/setup` 호출 시:

1. repo 내 모든 파일을 files 테이블에 등록한다.
   - 대상 확장자 파일: `is_target=1`, `status="pending"`
   - 비대상 파일: `is_target=0`, `status="skipped"`
2. 비대상 파일만 practice_root에 복사한다.
3. 대상 파일은 복사하지 않는다 (채점 통과 후 저장).

## 복사 규칙
연습 대상 확장자가 아닌 파일은 setup 단계에서 복사한다.

예:
```txt
repo/README.md → practice_root/README.md
repo/config.yaml → practice_root/config.yaml
```

## 대상 코드 파일 규칙
대상 확장자 파일은 처음에는 복사하지 않는다.
문제 통과 후 저장한다.

예:
```txt
repo/src/math_utils.py
→ 성공 후 practice_root/src/math_utils.py
```

## 덮어쓰기 규칙
- 이미 성공한 파일이 있으면 기본적으로 덮어쓰지 않는다.
- 사용자가 재제출 후 성공하면 확인 후 덮어쓴다.
- MVP-0에서는 `overwrite=true` 옵션이 있을 때만 덮어쓴다.

구체적 동작:
- `passed=true`이고 해당 경로에 파일이 없으면: 저장
- `passed=true`이고 파일이 있고 `overwrite=false`: 저장하지 않음
- `passed=true`이고 파일이 있고 `overwrite=true`: 덮어쓰기
- `passed=false`: 저장하지 않음

## 제외 경로
기본 제외:
```txt
.git/
__pycache__/
.venv/
venv/
node_modules/
.DS_Store
```

setup 복사와 Runner 임시 디렉토리 복사 모두 이 경로를 제외한다.
