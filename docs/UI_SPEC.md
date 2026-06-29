# UI_SPEC.md

## 라우팅
React Router를 사용한다.

```txt
/                 SetupPage
/projects/:id/analyze   AnalyzePage
/projects/:id/practice  PracticePage
```

## 상태 관리
MVP-0은 Zustand를 사용한다.

관리 상태:
- currentProject
- fileTree
- extensionStats
- problems
- currentProblem
- editorCode
- submitResult

## SetupPage
- repo path 입력
- practice root path 입력
- 분석 버튼
- 분석 성공 시 AnalyzePage로 이동

## AnalyzePage
- 파일 트리 표시
- 확장자 통계 표시
- 연습 대상 확장자 선택 (MVP-0은 `.py` 고정)
- 프로젝트 세팅 버튼

### AnalyzePage 동작 흐름
"프로젝트 세팅" 버튼 클릭 시:
1. `POST /api/projects/{project_id}/setup` 호출
2. 대상 파일 목록에 대해 `POST /api/projects/{project_id}/problems/generate` 순차 호출
3. 문제 생성 실패한 파일(TEST_NOT_FOUND, BASELINE_TEST_FAILED 등)은 건너뛴다
4. 완료 후 PracticePage로 이동

진행 상태:
- 세팅 중 로딩 표시
- 파일별 문제 생성 진행률 표시 (예: 3/12)
- 실패한 파일은 사유와 함께 목록 표시

## PracticePage
3열 구조:

```txt
[파일 트리] | [문제 / 코드 에디터] | [힌트 / 채점 결과]
```

왼쪽 영역:
- 대상 파일 목록 (문제가 생성된 파일만)
- 파일 클릭 시 해당 문제로 전환
- 통과 여부 아이콘 표시

가운데 영역:
- 위: 문제 설명 (prompt)
- 아래: Monaco Editor (starter_code 초기 로드)
- 제출 버튼

오른쪽 영역:
- 채점 결과 (passed/failed, stdout/stderr)
- MVP-0에서는 힌트 기능 미구현

## 반응형 규칙
- 최소 권장 너비: 1200px
- 1000px 미만: 오른쪽 Agent 패널 접기
- 800px 미만: 파일 트리도 접기
- 에디터 영역은 항상 우선 표시
