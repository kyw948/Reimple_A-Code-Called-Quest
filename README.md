# CodePractice

濡쒖뺄 repo瑜?肄붾뵫 ?곗뒿 ?꾨줈?앺듃濡?蹂?섑븯???깆씠??

## 援ъ“
```
backend/     - FastAPI ?쒕쾭
frontend/    - React ??samples/     - ?뚯뒪?몄슜 ?섑뵆 repo
docs/        - ?ㅺ퀎 臾몄꽌
```

## ?ㅽ뻾
```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install fastapi uvicorn pydantic sqlalchemy pytest pytest-json-report python-multipart
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

釉뚮씪?곗??먯꽌 http://localhost:5173 ?묒냽
