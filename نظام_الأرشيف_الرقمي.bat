@echo off
title Digital Archive Pro
color 1F

echo ==============================================
echo       Digital Archive Pro
echo ==============================================
echo.
echo جاري تشغيل Digital Archive Pro...
echo سيتم فتح المتصفح تلقائيًا بعد جاهزية الخادم المحلي.
echo.

cd /d "%~dp0"

:: التحقق من وجود Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [خطأ] تعذر العثور على Python في PATH.
    echo يرجى تثبيت Python 3.8.10 32-bit وإضافته إلى PATH.
    pause
    exit /b
)

:: يشغّل التطبيق الخادم محليًا ويفتح المتصفح بعد جاهزيته
python app.py

pause
