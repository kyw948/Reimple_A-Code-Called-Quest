# DEV_SETUP.md

## Backend
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn pydantic sqlalchemy pytest pytest-json-report python-multipart google-genai python-dotenv pymupdf requests
uvicorn app.main:app --reload --port 8000
```

## Frontend
```bash
cd frontend
npm install
npm run dev
```

## 환경변수
```env
DATABASE_URL=sqlite:///./app.db
SYNTAX_TIMEOUT_SECONDS=5
RUNNER_TEST_TIMEOUT_SECONDS=10
RUNNER_SUBMIT_TIMEOUT_SECONDS=30
FRONTEND_ORIGIN=http://localhost:5173
GITHUB_CLONE_BASE_PATH=~/.codepractice/repos
GEMINI_API_KEY=your-api-key-here
```

## CORS
Backend는 개발 환경에서 `FRONTEND_ORIGIN`을 허용한다.

## Desktop shell
MVP-0에서는 Tauri/Electron을 사용하지 않는다.
브라우저 + localhost 방식으로 시작한다.
