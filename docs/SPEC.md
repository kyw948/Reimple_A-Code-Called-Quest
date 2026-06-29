# SPEC.md

## 제품 개요
로컬 repo 또는 GitHub repo를 코딩 연습 프로젝트로 변환하는 로컬 실행형 앱이다.

## 핵심 흐름
1. 사용자가 `repo_path`와 `practice_root_path`를 입력한다.
2. 앱이 repo 파일 트리와 확장자 통계를 분석한다.
3. 사용자가 연습 대상 확장자를 선택한다. MVP-0은 `.py`만 지원한다.
4. 대상 확장자가 아닌 파일은 원본 트리 구조대로 `practice_root_path`에 복사한다.
5. 대상 코드 파일은 문제 후보로 분석한다.
6. 사용자가 문제를 풀고 제출한다.
7. pytest 채점 통과 시 해당 파일을 원래 경로에 저장한다.

## MVP-0 범위
- 로컬 repo path 입력
- 로컬 practice root path 입력
- Python `.py`만 지원
- 함수 빈칸 채우기 문제
- 기존 repo 내 pytest 테스트를 이용한 채점
- 성공 시 파일 저장
- 브라우저에서 `localhost`로 실행

## 제외 범위
- 웹 SaaS 배포
- 로그인/계정
- 다중 사용자
- 논문 figure 기반 문제 생성
- LLM 자동 문제 생성
- Docker sandbox
- GitHub clone 자동화

## 공통 결정사항
- API prefix는 `/api`로 통일한다.
- 프로젝트 ID는 UUID v4 문자열을 사용한다.
- `target_extensions`는 JSON 배열 문자열로 저장한다. 예: `[".py"]`
- MVP-0은 파일 1개당 문제 1개만 생성한다.
