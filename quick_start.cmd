@echo off
chcp 65001 >nul
echo 🚀 AgentKit Refactor 快速启动
echo ================================================

:: 检查Python环境
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python未安装，请先安装Python 3.11+
    pause
    exit /b 1
)

:: 检查.env文件
if not exist ".env" (
    echo ⚠️  .env文件不存在，创建示例配置...
    echo LLM_API_KEY=your_api_key_here> .env
    echo LLM_MODEL=deepseek-chat>> .env
    echo LLM_BASE_URL=https://api.deepseek.com>> .env
    echo LLM_TIMEOUT=120>> .env
    echo LLM_MAX_RETRIES=2>> .env
    echo AGENTKIT_LOG_DIR=logs>> .env
    echo ✅ 已创建.env示例文件
    echo 请编辑.env文件设置正确的LLM_API_KEY
    echo.
)

:: 安装依赖
echo 📦 1. 安装依赖...
if exist "uv.lock" (
    uv sync --extra dev
) else (
    pip install -e ".[dev]"
)

:: 创建日志目录
echo 📁 2. 创建日志目录...
mkdir logs 2>nul

:: 运行演示
echo 🎯 3. 运行演示...
if "%1"=="" (
    echo 请选择运行方式:
    echo 1. 快速测试 (默认)
    echo 2. CLI示例
    echo 3. 深度研究示例
    set /p choice=请输入选项 (1-3): 
    if "!choice!"=="1" (
        python run_agent.py
    ) else if "!choice!"=="2" (
        python scripts/chat.py general_chat "请介绍一下AgentKit Refactor项目"
    ) else if "!choice!"=="3" (
        python scripts/chat.py deep_research "人工智能的发展趋势"
    ) else (
        echo ❌ 无效选项，运行默认测试
        python run_agent.py
    )
) else (
    if "%1"=="chat" (
        python scripts/chat.py %2 %3 %4 %5
    ) else if "%1"=="research" (
        python scripts/chat.py deep_research %2 %3 %4 %5
    ) else (
        python run_agent.py
    )
)

echo ✅ 运行完成!
pause
