param(
    [string]$PythonExe = (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')
)

$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & pnpm.cmd --dir frontend build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    & $PythonExe -m PyInstaller --noconfirm --clean --onefile --windowed --name KasArchiveWorker --collect-all curl_cffi archive_desktop.py
    if ($LASTEXITCODE -ne 0) { throw 'Archive worker build failed' }
    & $PythonExe -m PyInstaller --noconfirm --clean --onefile --windowed --name KasControlModern --icon 'assets/kas-control.ico' --collect-all curl_cffi --add-data 'assets/kas-control.ico;assets' --add-data 'frontend/dist;frontend/dist' desktop.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
} finally { Pop-Location }
