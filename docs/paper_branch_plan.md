# 논문 기반 분기 계획

## 흐름

```
SetupPage: [로컬 경로] [GitHub URL] [논문]
                                      ↓
                              arXiv URL 또는 PDF 업로드
                                      ↓
                          논문 메타데이터 + 본문 추출
                                      ↓
                     Paper2Code 정방향: Planning → Analysis → Coding
                                      ↓
                          참조 코드 생성 (임시 repo)
                                      ↓
                     기존 파이프라인: analyze → assess → generate → practice
```

## 구현 단계

### 1단계: 논문 입력 + 본문 추출
- SetupPage에 "논문" 탭 추가
- arXiv URL 입력 → arXiv API로 abstract + PDF URL → PDF 다운로드 → 텍스트 추출
- PDF 직접 업로드 → 텍스트 추출
- PyMuPDF(fitz) 또는 pdfplumber로 PDF 파싱

### 2단계: Paper2Code Planning
- Overall Plan: 논문에서 구현할 핵심 컴포넌트 추출
- Architecture Design: 파일 구조 + 클래스 설계
- Logic Design: 파일 간 의존성 + 구현 순서
- Configuration: 하이퍼파라미터 등

### 3단계: Paper2Code Analysis + Coding
- 각 파일의 구현 명세 작성
- LLM이 파일을 순차 생성 (이전 파일 참조)
- 생성된 코드를 임시 디렉토리에 저장 (repo처럼)

### 4단계: 기존 파이프라인 연결
- 생성된 임시 repo를 repo_path로 설정
- 기존 analyze → assess → generate → practice 실행
- 문제 설명에 논문 원문 맥락 추가

## DB 변경

### projects 테이블 추가
```sql
paper_source TEXT        -- 'arxiv_url', 'pdf_upload', null(코드 기반)
paper_url TEXT           -- arXiv URL
paper_title TEXT
paper_abstract TEXT
paper_content TEXT       -- 추출된 논문 전문 (or 요약)
paper_metadata TEXT      -- JSON: authors, year, venue 등
generated_repo_path TEXT -- Paper2Code로 생성된 코드 경로
```

## API 추가

### POST /api/papers/parse
논문을 입력받아 메타데이터 + 본문을 추출한다.

Request (arXiv):
```json
{
  "arxiv_url": "https://arxiv.org/abs/2204.12484"
}
```

Request (PDF upload):
multipart/form-data로 PDF 파일

Response:
```json
{
  "title": "ViTPose: Simple Vision Transformer Baselines for Human Pose Estimation",
  "abstract": "...",
  "authors": ["Yufei Xu", "..."],
  "content": "추출된 본문 텍스트 (섹션별)",
  "year": 2022
}
```

### POST /api/papers/generate-code
Paper2Code 파이프라인으로 참조 코드를 생성한다.

Request:
```json
{
  "project_id": "uuid",
  "paper_content": "논문 본문"
}
```

이 API는 시간이 오래 걸리므로 백그라운드로 실행.
상태 폴링: GET /api/papers/{project_id}/codegen-status
