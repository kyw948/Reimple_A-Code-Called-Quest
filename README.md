# Reimple
> a code called quest

AI 논문과 코드 repository를 재구현하면서 배우는 코딩 연습 도구.

## 빠른 시작

### 요구사항
- Python 3.10+
- Node.js 18+
- Git

### 실행
```powershell
# Windows
./start.ps1
```

```bash
# Mac/Linux
chmod +x start.sh
./start.sh
```

브라우저에서 `http://localhost:5173` 접속.

### Gemini API 키
LLM 기능(파일 분석, 채점, 힌트)을 사용하려면 [Google AI Studio](https://aistudio.google.com/)에서 API 키를 발급받아 `backend/.env`에 설정.

## 사용법

### 코드 기반 (GitHub repo)
1. GitHub URL 또는 로컬  입력
2. 프로젝트 분석 → 문제 트리 생성
3. 함수 단위로 코딩 연습

### 논문 기반 (arXiv / PDF)
1. arXiv URL 또는 PDF 업로드
2. Paper2Code 방식으로 참조 코드 생성
3. 생성된 코드 기반으로 코딩 연습
