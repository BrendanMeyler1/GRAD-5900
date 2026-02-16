@echo off
echo Starting Excel Agent Server...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed or not in PATH.
    pause
    exit /b
)

echo Open http://127.0.0.1:8000 in your browser.
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
if %errorlevel% neq 0 (
    echo Server failed to start.
)
pause
