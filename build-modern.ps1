$ErrorActionPreference = 'Stop'
$PythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
Push-Location $PSScriptRoot
try {
    & $PythonExe -m PyInstaller --noconfirm --clean --onefile --windowed --name KasControlModern --icon 'assets/kas-control.ico' --collect-all curl_cffi --add-data 'assets/kas-control.ico;assets' --add-data 'frontend/dist;frontend/dist' desktop.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
} finally { Pop-Location }
