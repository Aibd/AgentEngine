@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
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
    py -m uv --version >nul 2>&1
    if errorlevel 1 (
        where uv >nul 2>&1
        if errorlevel 1 (
            echo uv not found. Please install uv or remove uv.lock to use pip fallback.
            pause
            exit /b 1
        )
        set "UV_CMD=uv"
    ) else (
        set "UV_CMD=py -m uv"
    )
    !UV_CMD! sync --extra dev
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
    set "PYTHON_RUN=.venv\Scripts\python.exe"
    if not exist "!PYTHON_RUN!" (
        set "PYTHON_RUN=py"
    )
    set "UVICORN_RUN=!PYTHON_RUN! -m uvicorn"
) else (
    py -m pip install -e ".[dev]"
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
    set "UVICORN_RUN=py -m uvicorn"
)
mkdir logs 2>nul
mkdir data 2>nul

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
start "AgentEngine Backend" cmd /k "%UVICORN_RUN% examples.services.web_api:app --host 127.0.0.1 --port 8000 --log-level info"

set "FRONTEND_STARTED=0"
if not exist "examples\web\package.json" (
    echo   Frontend package not found; skipping frontend startup.
    goto after_frontend_start
)
where npm >nul 2>&1
if errorlevel 1 (
    echo   npm not found; skipping frontend startup.
    goto after_frontend_start
)
start "AgentEngine Frontend" cmd /k "cd /d examples\web && (if exist node_modules (echo Dependencies already installed.) else (npm install)) && npm run dev"
set "FRONTEND_STARTED=1"
:after_frontend_start

echo.
echo ================================================
echo   Backend:  http://127.0.0.1:8000
if "%FRONTEND_STARTED%"=="1" (
    echo   Frontend: http://127.0.0.1:5173
) else (
    echo   Frontend: skipped
)
echo   Close the terminal windows to stop.
echo ================================================
echo.

:: --- Service checks ---
echo Checking services...
call :wait_for_url "Backend" "http://127.0.0.1:8000/api/health"
if "%FRONTEND_STARTED%"=="1" (
    call :wait_for_url "Frontend" "http://127.0.0.1:5173"
)

echo.
pause
exit /b 0

:wait_for_url
set "SERVICE_NAME=%~1"
set "SERVICE_URL=%~2"
for /l %%i in (1,1,30) do (
    curl -fsS "%SERVICE_URL%" >nul 2>&1
    if not errorlevel 1 (
        echo   %SERVICE_NAME% is ready: %SERVICE_URL%
        exit /b 0
    )
    timeout /t 1 /nobreak >nul
)
echo   %SERVICE_NAME% did not answer at %SERVICE_URL%. Check the %SERVICE_NAME% terminal window.
exit /b 1
