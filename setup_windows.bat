@echo off
setlocal

echo [Agent Navigator] setup_windows.bat archived as compatibility wrapper.
echo.
echo Canonical Windows host bootstrap:
echo   powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1
echo.
echo Running canonical installer now...
echo.

powershell -ExecutionPolicy Bypass -File scripts/install/install_windows.ps1 %*
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
  echo.
  echo [ERROR] Canonical installer failed with code %EXIT_CODE%.
)

exit /b %EXIT_CODE%
