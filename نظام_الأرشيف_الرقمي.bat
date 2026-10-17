@echo off
setlocal
title Digital Archive Pro
color 1F
cd /d "%~dp0"

echo ==============================================
echo       Digital Archive Pro
echo       © Noureddine 2026
echo ==============================================
echo.
echo جاري التحقق من Python...

set "PYTHON_CMD="
set "PYTHON_ARGS="

python -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_CMD=python"
  goto python_ready
)

py -3.8 -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_CMD=py"
  set "PYTHON_ARGS=-3.8"
  goto python_ready
)

py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_CMD=py"
  set "PYTHON_ARGS=-3"
  goto python_ready
)

echo.
echo [خطأ] لم يتم العثور على Python 3.8 أو أحدث.
echo هذا الإصدار من البرنامج يستخدم مكتبات Python القياسية فقط.
echo لا يوجد تثبيت تلقائي ولا يحتاج التشغيل إلى الإنترنت.
echo تحقق من تثبيت Python وإضافته إلى PATH، ثم أعد المحاولة.
echo ملاحظة: Windows 7 يحتاج عادةً إلى Python 3.8 إصدار 32-bit.
pause
exit /b 1

:python_ready
if not exist "app.py" (
  echo [خطأ] الملف app.py غير موجود. أعد استخراج الحزمة كاملة.
  pause
  exit /b 2
)
if not exist "static\style.css" (
  echo [خطأ] ملف الواجهة static\style.css غير موجود. أعد استخراج الحزمة كاملة.
  pause
  exit /b 3
)

echo تم العثور على Python المناسب.
echo جاري تشغيل الخادم المحلي...
echo اترك هذه النافذة مفتوحة أثناء استخدام البرنامج.
echo لإيقاف البرنامج، أغلقه من الواجهة ثم انتظر انتهاء هذه النافذة.
echo.
%PYTHON_CMD% %PYTHON_ARGS% app.py
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
  echo.
  echo انتهى البرنامج برمز خطأ: %APP_EXIT%
  echo احتفظ بهذه الرسالة عند طلب الدعم.
  pause
)
exit /b %APP_EXIT%
