@echo off
chcp 65001 >nul
echo AgentEngine Quick Start
echo ================================================

:: Check Python
py --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Please install Python 3.11+
    pause
    exit /b 1
)

:: Check .env
if not exist ".env" (
    echo .env not found, creating template...
    echo LLM_API_KEY=your_api_key_here> .env
    echo LLM_MODEL=deepseek-chat>> .env
    echo LLM_BASE_URL=https://api.deepseek.com/v1>> .env
    echo LLM_TIMEOUT=120>> .env
    echo LLM_MAX_RETRIES=2>> .env
    echo AGENTENGINE_LOG_DIR=logs>> .env
    echo Created .env template. Please edit LLM_API_KEY before running.
    pause
    exit /b 1
)

:: Install dependencies
echo [1/3] Installing dependencies...
if exist "uv.lock" (
    uv sync --extra dev
) else (
    py -m pip install -e ".[dev]"
)
mkdir logs 2>nul

:: --- Kill existing processes on port 8000 / 5173 ---
echo [2/3] Cleaning up ports...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a on port 8000
    taskkill /PID %%a /F >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    echo   Killing PID %%a on port 5173
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: --- Start backend + frontend ---
echo [3/3] Starting services...
start "AgentEngine Backend" cmd /c "uv run --env-file .env uvicorn examples.services.web_api:app --host 127.0.0.1 --port 8000 --log-level warning"
if exist "examples\web\package.json" (
    start "AgentEngine Frontend" cmd /c "cd examples\web && if not exist node_modules npm install && npm run dev"
)

echo.
echo ================================================
echo   Backend:  http://127.0.0.1:8000
echo   Frontend: http://localhost:5173
echo   Close the terminal windows to stop.
echo ================================================
echo.

:: --- Quick smoke test ---
echo Running smoke test...
timeout /t 3 /nobreak >nul
PYTHONIOENCODING=utf-8 py scripts\chat.py general_chat "hello"

echo.
pause
