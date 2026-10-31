<#
.SYNOPSIS
Start the isolated fictional showcase with backend-supervised local inference.
.DESCRIPTION
Requires a prepared showcase vault and installed assets. It never seeds data,
downloads models, or kills unrelated processes. Stop this launcher to stop only
the backend and frontend processes that it created. The normal Eva launchers
remain the entry points for a personal vault.
#>
param(
    [switch]$Headless,
    [switch]$PauseConsolidation,
    [string]$LlamaProfile = 'win-cuda'
)

$ErrorActionPreference = 'Stop'
$evaRoot = Split-Path -Parent $PSScriptRoot
$evaVault = Join-Path $evaRoot 'local_vault'
$marker = Join-Path $evaVault 'showcase-run.json'
if (!(Test-Path -LiteralPath $marker)) {
    throw 'Prepare an empty showcase vault with build_showcase.py --prepare-only first.'
}
$fixture = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
if ($fixture.fictional -ne $true) { throw 'This launcher requires a fictional showcase vault.' }

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:EVA_VAULT_DIR = $evaVault
$env:EVA_START_LLAMA = '1'
$env:EVA_LLAMA_PROFILE = $LlamaProfile
# CTranslate2's CUDA 12 speech runtime differs from llama.cpp's CUDA 13 build.
# CPU/int8 keeps speech usable without installing a second CUDA toolkit.
$env:EVA_WHISPER_DEVICE = 'cpu'
if ($PauseConsolidation) {
    $env:EVA_START_SCHEDULER = '0'
} else {
    $env:EVA_START_SCHEDULER = '1'
}

function Open-ShowcaseWindow {
    <# Open the installed native window, with a browser fallback during builds. #>
    $native = Join-Path $evaRoot 'ui\src-tauri\target\release\eva.exe'
    if (Test-Path -LiteralPath $native) {
        Start-Process -FilePath $native -WorkingDirectory $evaRoot
    } else {
        Start-Process 'http://localhost:1420'
    }
}

$activePorts = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @(8000,1420,11500) })
if ($activePorts.Count -gt 0) {
    $health = $null
    try { $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 3 } catch { }
    $expectedModel = Join-Path $evaRoot 'models\gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf'
    if ($health.model.model_path -eq $expectedModel -and $health.model_server_running) {
        if (!$Headless) { Open-ShowcaseWindow }
        return
    }
    throw 'Eva ports are already in use by another or incomplete instance. No processes were stopped.'
}

$logs = Join-Path $evaVault 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$backend = $null
$frontend = $null
try {
    $backend = Start-Process -FilePath (Join-Path $evaRoot 'backend\.venv\Scripts\python.exe') `
        -ArgumentList @('-m','uvicorn','app:app','--host','127.0.0.1','--port','8000') `
        -WorkingDirectory (Join-Path $evaRoot 'backend') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs 'backend.log') `
        -RedirectStandardError (Join-Path $logs 'backend.err')
    $frontend = Start-Process -FilePath (Get-Command node.exe).Source `
        -ArgumentList @((Join-Path $evaRoot 'ui\node_modules\vite\bin\vite.js'),'--host','127.0.0.1') `
        -WorkingDirectory (Join-Path $evaRoot 'ui') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs 'frontend.log') `
        -RedirectStandardError (Join-Path $logs 'frontend.err')
    @{backend=$backend.Id;frontend=$frontend.Id;fictional=$true} | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $logs 'runtime-pids.json') -Encoding UTF8
    if (!$Headless) {
        Open-ShowcaseWindow
    }
    while (!$backend.HasExited -and !$frontend.HasExited) {
        Start-Sleep -Seconds 2
        $backend.Refresh()
        $frontend.Refresh()
    }
    throw 'An Eva service exited; inspect local_vault/logs for details.'
} finally {
    foreach ($service in @($backend,$frontend)) {
        if ($service -and !$service.HasExited) {
            try { & taskkill.exe /PID $service.Id /T /F 2>$null | Out-Null } catch { }
        }
    }
}
