@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo No .venv. Create it first: python -m venv .venv
  exit /b 1
)

"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

"%PYTHON%" -m PyInstaller --noconfirm --clean --onefile --console ^
  --name PDFsplitter ^
  --collect-all pikepdf ^
  --workpath build ^
  --distpath dist ^
  --specpath build ^
  split_pdf.py
if errorlevel 1 exit /b 1

"%PYTHON%" -m PyInstaller --noconfirm --onefile --windowed ^
  --name PDFsplitterGUI ^
  --collect-all pikepdf ^
  --collect-all pypdfium2 ^
  --collect-all pypdfium2_raw ^
  --hidden-import PIL.ImageTk ^
  --hidden-import split_pdf ^
  --workpath build ^
  --distpath dist ^
  --specpath build ^
  split_gui.py
if errorlevel 1 exit /b 1

echo OK: dist\PDFsplitter.exe
echo OK: dist\PDFsplitterGUI.exe
endlocal
