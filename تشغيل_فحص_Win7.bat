@echo off
cd /d "%~dp0"
start "فحص توافق Digital Archive Pro" cmd /k "cd /d ""%~dp0"" && echo ============================================== && echo   فحص توافق Digital Archive Pro && echo   Windows 7 / 10 / 11 - 32/64-bit && echo ============================================== && echo. && echo جاري تشغيل الفحص... && echo. && python tests\windows_qa.py && echo. && echo انتهى الفحص. && echo راجع Windows_Compatibility_QA_Report.txt عند الحاجة. || echo. && echo حدث خطأ أثناء الفحص. && echo راجع Windows_Compatibility_QA_Report.txt"
exit /b 0
