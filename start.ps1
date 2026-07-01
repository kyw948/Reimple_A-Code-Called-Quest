Write-Host "=== Reimple: a code called quest ===" -ForegroundColor Cyan
Write-Host ""

# Backend setup
Write-Host "[1/4] Backend 환경 설정..." -ForegroundColor Yellow
Set-Location backend
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
.\.venv\Scripts\activate
pip install -r requirements.txt -q

# .env 확인
if (-not (Test-Path ".env")) {
    Write-Host ""
    Write-Host "GEMINI_API_KEY가 필요합니다." -ForegroundColor Red
    $key = Read-Host "API 키를 입력하세요 (없으면 Enter)"
    if ($key) {
        "GEMINI_API_KEY=$key" | Out-File -FilePath .env -Encoding utf8
    } else {
        "GEMINI_API_KEY=" | Out-File -FilePath .env -Encoding utf8
        Write-Host "API 키 없이 시작합니다. LLM 기능은 동작하지 않습니다." -ForegroundColor Yellow
    }
}

# Backend 시작 (백그라운드)
Write-Host "[2/4] Backend 서버 시작..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-Command", "cd $PWD; .\.venv\Scripts\activate; uvicorn app.main:app --reload --port 8000" -WindowStyle Minimized
Set-Location ..

# Frontend setup
Write-Host "[3/4] Frontend 설정..." -ForegroundColor Yellow
Set-Location frontend
if (-not (Test-Path "node_modules")) {
    npm install
}

# Frontend 시작
Write-Host "[4/4] Frontend 시작..." -ForegroundColor Yellow
Write-Host ""
Write-Host "브라우저에서 http://localhost:5173 을 열어주세요" -ForegroundColor Green
Write-Host ""
npm run dev
