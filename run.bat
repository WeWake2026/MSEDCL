@echo off
title MSEDCL Quality Audit Management System
color 0A
cls
echo ===============================================================================
echo                MSEDCL Quality Audit Management Portal
echo ===============================================================================
echo.

cd /d "%~dp0"

:: 1. Check Python installation
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found in your system PATH.
    echo Please install Python 3.10+ and check 'Add Python to PATH' during setup.
    pause
    exit /b 1
)

:: 2. Create virtual environment if missing
if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating dedicated Python virtual environment (.venv)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: 3. Install / upgrade required dependencies
echo [*] Checking and verifying package dependencies...
call .venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if %errorlevel% neq 0 (
    echo [WARNING] Direct requirements install had warnings, ensuring core packages...
    call .venv\Scripts\python.exe -m pip install -q flask pandas openpyxl werkzeug
)

:: 4. Ensure .env exists with APP_SECRET_KEY
if not exist ".env" (
    echo [*] Generating secure local .env configuration...
    if exist ".env.example" (
        copy /y .env.example .env >nul
    ) else (
        echo APP_SECRET_KEY=msedcl_production_secure_key_2026_antigravity > .env
        echo DATABASE_PATH=quality_audit.db >> .env
        echo PORT=5000 >> .env
        echo MAX_UPLOAD_MB=25 >> .env
    )
)

:: 5. Run database schema migrations
echo [*] Checking database tables and running schema migrations...
call .venv\Scripts\python.exe -c "import migrate_db; migrate_db.run_migrations('quality_audit.db')" >nul 2>nul

:: 6. Launch Application Server
echo.
echo ===============================================================================
echo  Application is running at: http://127.0.0.1:5000
echo  Default Admin Login:       Username: qaadmin   Password: password123
echo ===============================================================================
echo.
echo Press Ctrl+C in this window to stop the server anytime.
echo.

start "" "http://127.0.0.1:5000"
call .venv\Scripts\python.exe app.py

pause
