# MVP.md

## 목표
로컬 Python repo를 대상으로 함수 빈칸 채우기 문제를 만들고 pytest로 채점한다.

## 포함 기능
- repo path 검증
- 파일 트리 생성
- 확장자 통계 생성
- 프로젝트 생성/목록 조회
- `.py` 파일 분석
- 파일당 문제 1개 생성
- 테스트 파일 매칭
- 코드 제출
- pytest 실행
- 통과 시 practice root에 저장

## 제외 기능
- GitHub URL clone
- LLM 문제 생성
- 논문/모델 figure 문제
- 여러 언어 지원
- 한 파일 여러 문제 생성
- Docker sandbox

## 완료 기준
- 샘플 repo 분석 가능
- `.py` 파일 문제 생성 가능
- 제출 코드가 pytest 통과/실패로 판정됨
- 통과한 파일이 practice root의 원래 경로에 저장됨
