#!/bin/bash
echo "=== Reimple: a code called quest ==="
echo ""

# Backend
echo "[1/4] Backend 환경 설정..."
cd backend
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt -q

if [ ! -f ".env" ]; then
    echo ""
    echo "GEMINI_API_KEY가 필요합니다."
    read -p "API 키를 입력하세요 (없으면 Enter): " key
    echo "GEMINI_API_KEY=$key" > .env
fi

echo "[2/4] Backend 서버 시작..."
uvicorn app.main:app --reload --port 8000 &
cd ..

# Frontend
echo "[3/4] Frontend 설정..."
cd frontend
if [ ! -d "node_modules" ]; then
    npm install
fi

echo "[4/4] Frontend 시작..."
echo ""
echo "브라우저에서 http://localhost:5173 을 열어주세요"
echo ""
npm run dev
