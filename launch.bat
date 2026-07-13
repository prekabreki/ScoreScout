@echo off
setlocal
cd /d "%~dp0"

REM Pick an interpreter: a %PYTHON% override wins, then the project venv
REM (where the dependencies get installed), then a system Python. The `py`
REM launcher is preferred over `python` (which may be a Store stub).
if defined PYTHON goto check
if exist ".venv\Scripts\python.exe" (set "PYTHON=.venv\Scripts\python.exe" & goto check)
where py >nul 2>&1 && (set "PYTHON=py" & goto check)
where python >nul 2>&1 && (set "PYTHON=python" & goto check)
echo Error: no Python interpreter found. Install Python 3.10+ or set PYTHON.
pause
exit /b 1

:check
REM Fail early with an actionable message if dependencies aren't installed,
REM instead of dying on an ImportError deep inside app.py.
"%PYTHON%" -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('flask') and u.find_spec('music21') else 1)" >nul 2>&1
if errorlevel 1 (
    echo Error: dependencies are missing for "%PYTHON%".
    echo Install them with:
    echo     "%PYTHON%" -m pip install -r requirements.txt
    pause
    exit /b 1
)

"%PYTHON%" app.py
pause
