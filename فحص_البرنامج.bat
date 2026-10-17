@echo off
setlocal
title Digital Archive Pro - QA
cd /d "%~dp0"
set "PYTHON_CMD="
set "PYTHON_ARGS="

python -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_CMD=python"
  goto run_tests
)

py -3.8 -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_CMD=py"
  set "PYTHON_ARGS=-3.8"
  goto run_tests
)

py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_CMD=py"
  set "PYTHON_ARGS=-3"
  goto run_tests
)

echo [ERROR] Python 3.8 or newer was not found.
pause
exit /b 1

:run_tests
echo This runs tests against temporary test data, not your live archive.
echo.
%PYTHON_CMD% %PYTHON_ARGS% -m py_compile app.py
if errorlevel 1 goto failed
%PYTHON_CMD% %PYTHON_ARGS% -m unittest tests.test_safety_guards
if errorlevel 1 goto failed
%PYTHON_CMD% %PYTHON_ARGS% tests\qa_smoke.py
if errorlevel 1 goto failed
%PYTHON_CMD% %PYTHON_ARGS% tests\windows_qa.py
if errorlevel 1 goto failed
echo.
echo QA commands completed successfully on this computer.
echo This does not certify compatibility on other Windows versions.
pause
exit /b 0

:failed
echo.
echo QA FAILED. Save the error text shown above.
pause
exit /b 1
