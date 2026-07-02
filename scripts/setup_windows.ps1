param(
  [switch]$SkipModel,
  [switch]$SkipVoice,
  [switch]$Force,
  [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
if ($InstallDir -eq "") {
  $InstallDir = Join-Path $Root "models"
}
$ModelDir = $InstallDir
$ModelFile = "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf"
$ModelPath = Join-Path $ModelDir $ModelFile
$ModelUrl = "https://huggingface.co/unsloth/gemma-4-E2B-it-qat-GGUF/resolve/main/$ModelFile" + "?download=true"
$LlamaDir = Join-Path $Root "bin\llama.cpp\windows"
$LlamaExe = Join-Path $LlamaDir "llama-server.exe"

function Say($Message) {
  Write-Host "[setup-windows] $Message"
}

function Ensure-Dir($Path) {
  if (!(Test-Path $Path)) {
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
  }
}

function Assert-FreeSpace($Path, [Int64]$RequiredBytes) {
  Ensure-Dir $Path
  $drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot((Resolve-Path $Path)).Substring(0,1))
  if ($drive.Free -lt $RequiredBytes) {
    throw "Not enough free space on $($drive.Name):. Need at least $([math]::Round($RequiredBytes / 1GB, 1)) GB."
  }
}

Say "Preparing Eva local AI assets for Windows."
Ensure-Dir $ModelDir
Ensure-Dir $LlamaDir

if (!(Test-Path $LlamaExe) -or $Force) {
  Say "Downloading latest llama.cpp Windows CPU release."
  $Release = Invoke-RestMethod -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
  $Asset = $Release.assets |
    Where-Object { $_.name -match "win" -and $_.name -match "x64" -and $_.name -match "cpu" -and $_.name -match "\.zip$" } |
    Select-Object -First 1
  if ($null -eq $Asset) {
    throw "Could not find a Windows x64 CPU llama.cpp release asset."
  }
  $Zip = Join-Path $LlamaDir $Asset.name
  $Extract = Join-Path $LlamaDir "extract"
  Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $Zip -UseBasicParsing
  if (Test-Path $Extract) { Remove-Item -Recurse -Force $Extract }
  Expand-Archive -Force $Zip $Extract
  $Found = Get-ChildItem -Path $Extract -Recurse -Filter "llama-server.exe" | Select-Object -First 1
  if ($null -eq $Found) {
    throw "Downloaded llama.cpp archive did not contain llama-server.exe."
  }
  Copy-Item -Force $Found.FullName $LlamaExe
  Say "Installed llama-server.exe at $LlamaExe"
} else {
  Say "Found llama-server.exe at $LlamaExe"
}

if (!$SkipModel) {
  if ((Test-Path $ModelPath) -and !$Force) {
    Say "Model already present at $ModelPath"
  } else {
    Assert-FreeSpace $ModelDir (5GB)
    $Part = "$ModelPath.part"
    Say "Downloading Gemma model to $ModelPath"
    try {
      Invoke-WebRequest -Uri $ModelUrl -OutFile $Part -UseBasicParsing
      if ((Get-Item $Part).Length -le 0) {
        throw "Downloaded file is empty."
      }
      Move-Item -Force $Part $ModelPath
      Say "Model download complete."
    } catch {
      if (Test-Path $Part) { Remove-Item -Force $Part }
      throw "Model download failed: $($_.Exception.Message)"
    }
  }
} else {
  Say "Skipping model download."
}

if (!$SkipVoice) {
  Say "Voice assets are managed by Eva's app setup or the existing voice download scripts."
} else {
  Say "Skipping voice assets."
}

Say "Done."
