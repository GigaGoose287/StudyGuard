@echo off
setlocal
cd /d "%~dp0"
title Building StudyGuard

set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 set "PY=py"
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found.
  echo Install it from https://www.python.org/downloads/  and tick "Add python.exe to PATH", then run this again.
  pause
  exit /b 1
)

echo Installing the packager (one-time)...
%PY% -m pip install --upgrade pyinstaller
if errorlevel 1 goto fail

echo Building StudyGuard.exe - this takes a minute or two...
%PY% -m PyInstaller --noconfirm --clean StudyGuard.spec
if errorlevel 1 goto fail
if not exist "dist\StudyGuard.exe" goto fail

set "DESK="
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESK=%%D"
if "%DESK%"=="" set "DESK=%USERPROFILE%\Desktop"

copy /y "dist\StudyGuard.exe" "%DESK%\StudyGuard.exe" >nul
if errorlevel 1 goto fail

rmdir /s /q build >nul 2>&1
rmdir /s /q dist >nul 2>&1
del /q StudyGuard.spec >nul 2>&1

echo.
echo Done! StudyGuard.exe is on your Desktop. Double-click it to start.
echo Your data lives in %USERPROFILE%\.studyguard  (it is kept when you rebuild).
pause
exit /b 0

:fail
echo.
echo Build failed - see the messages above.
pause
exit /b 1
