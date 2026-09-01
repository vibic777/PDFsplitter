@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist build rd /s /q build
if exist dist rd /s /q dist
if exist PDFsplitter.spec del /q PDFsplitter.spec
if exist PDFsplitterGUI.spec del /q PDFsplitterGUI.spec
if exist .pytest_cache rd /s /q .pytest_cache

for /d /r %%d in (__pycache__) do (
  echo %%d | find /I "\.venv\" >nul
  if errorlevel 1 if exist "%%d" rd /s /q "%%d"
)

for /r %%f in (*.pyc) do (
  echo %%f | find /I "\.venv\" >nul
  if errorlevel 1 if exist "%%f" del /q "%%f"
)
for /r %%f in (*.pyo) do (
  echo %%f | find /I "\.venv\" >nul
  if errorlevel 1 if exist "%%f" del /q "%%f"
)

echo OK: project garbage removed
endlocal
