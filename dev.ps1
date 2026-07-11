# Eva - dev launcher (PowerShell / Windows).
#
# Windows-native equivalent of dev.sh. Starts the WHOLE stack with one command:
#   1. The native llama.cpp `llama-server` model server on 127.0.0.1:11500
#      (launched & supervised by the backend via EVA_START_LLAMA=1).
#   2. The Python FastAPI backend (uvicorn) on 127.0.0.1:8000.
#   3. The frontend - the Tauri native shell if Rust is installed (`tauri dev`,
#      which itself starts Vite), otherwise the bare Vite dev server at
#      http://localhost:1420.
#
# Run it from PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\dev.ps1
#   (or, if your policy already allows local scripts:  .\dev.ps1 )
#
# Press Ctrl-C to tear everything down.

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# UTF-8 output - the download/status scripts print non-ASCII (e.g. the arrow
# glyph), which crashes on Windows' default cp1252 console without this.
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8       = '1'
# The backend launches & supervises the llama.cpp model server as a sidecar.
$env:EVA_START_LLAMA  = '1'

# --- pick the backend Python interpreter -----------------------------------
function Test-Deps([string]$py) {
    # A bare import list has no quotes for PowerShell to mangle when it hands the
    # -c argument to python.exe. A missing module raises ImportError -> exit 1.
    & $py -c "import fastapi, uvicorn, chromadb, fastembed" 2>$null
    return ($LASTEXITCODE -eq 0)
}

$VenvPy = Join-Path $Root 'backend\.venv\Scripts\python.exe'
$PyBin  = $null

if (Test-Path $VenvPy) {
    $PyBin = $VenvPy
} else {
    # Prefer an interpreter that ALREADY has the deps (no venv rebuild).
    foreach ($cand in @('python', 'python3')) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd -and (Test-Deps $cmd.Source)) { $PyBin = $cmd.Source; break }
    }
    if (-not $PyBin) {
        # First-run: create the venv and install core requirements.
        $sys = Get-Command python -ErrorAction SilentlyContinue
        if (-not $sys) { $sys = Get-Command python3 -ErrorAction SilentlyContinue }
        if (-not $sys) { throw "No python/python3 found on PATH." }
        Write-Host "[dev] creating backend venv (first run)..."
        & $sys.Source -m venv (Join-Path $Root 'backend\.venv')
        $PyBin = $VenvPy
        & $PyBin -m pip install --upgrade pip | Out-Null
        & $PyBin -m pip install -r (Join-Path $Root 'backend\requirements.txt')
        Write-Host "[dev] (voice is optional: pip install -r backend\requirements-voice.txt)"
    }
}

if ($PyBin -eq $VenvPy) {
    Write-Host "[dev] backend Python: venv ($PyBin)"
} else {
    Write-Host "[dev] backend Python: system ($PyBin) - deps already present, skipping venv."
}

# --- readiness feedback: model + llama-server binary (non-fatal) -----------
# Pure PowerShell (no python -c, whose embedded quotes PowerShell mangles). These
# are the default locations backend/llm/server.py resolves on Windows; a settings
# override of local_model_path isn't reflected here but the backend still honors it.
$modelPath = Join-Path $Root 'models\gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf'
$llamaExe  = Join-Path $Root 'bin\llama.cpp\windows\llama-server.exe'
Write-Host "[dev] model present : $(Test-Path $modelPath)  ($modelPath)"
if (Test-Path $llamaExe) {
    Write-Host "[dev] llama-server  : $llamaExe"
} else {
    Write-Host "[dev] llama-server  : NOT FOUND on PATH or bin\llama.cpp\windows - run scripts\setup_windows.ps1"
}

# --- start backend (+ model server sidecar) --------------------------------
Write-Host "[dev] starting backend on http://127.0.0.1:8000  (model server on :11500) ..."
$backend = Start-Process -PassThru -NoNewWindow -FilePath $PyBin `
    -ArgumentList @('-m','uvicorn','app:app','--host','127.0.0.1','--port','8000','--reload') `
    -WorkingDirectory (Join-Path $Root 'backend')

try {
    # --- frontend ----------------------------------------------------------
    Set-Location (Join-Path $Root 'ui')
    if (-not (Test-Path 'node_modules')) {
        Write-Host "[dev] installing frontend deps (first run)..."
        npm install
    }

    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        Write-Host "[dev] starting Tauri shell (cargo found) ..."
        Write-Host "[dev] NOTE: the FIRST launch compiles Rust from scratch (several minutes);"
        Write-Host "[dev]       the native window appears once that finishes. Later runs are fast."
        npm run tauri dev
    } else {
        Write-Host "[dev] cargo/rustc not found - starting Vite only (open http://localhost:1420)."
        Write-Host "[dev] install Rust (https://rustup.rs) then re-run to get the native window."
        npm run dev
    }
}
finally {
    Write-Host ""
    Write-Host "[dev] shutting down..."
    # /T kills the uvicorn reloader's child processes too.
    if ($backend -and -not $backend.HasExited) {
        taskkill /PID $backend.Id /T /F 2>$null | Out-Null
    }
    # The supervised model server is a separate process - sweep it explicitly.
    taskkill /IM llama-server.exe /F 2>$null | Out-Null
    Set-Location $Root
}
