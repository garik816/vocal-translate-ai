# Launch the local ACE-Step 1.5 REST API in the current terminal.
# On first use this installs ACE-Step into a sibling directory; no elevated
# privileges and no changes to the Vocal Translate AI Python environment.
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

$HealthUrl = 'http://127.0.0.1:8001/health'
function Test-AceHealth {
    try {
        $response = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 3 -ErrorAction Stop
        $httpReady = $response.code -eq 200 -or $response.data.status -eq 'ok'
        # /health can answer before DiT weights finish initializing.
        return $httpReady -and $response.data.models_initialized -eq $true
    } catch {
        return $false
    }
}

if (Test-AceHealth) {
    Write-Host "[ACE] API already running: $HealthUrl" -ForegroundColor Green
    Write-Host "Open another PowerShell in this project and run: .\ACE_ONLY.bat"
    exit 0
}

$AceDir = if ($env:ACE_STEP_DIR) {
    $env:ACE_STEP_DIR
} else {
    Join-Path (Split-Path $Root -Parent) 'ACE-Step-1.5'
}
$AceDir = [IO.Path]::GetFullPath($AceDir)
Write-Host "[ACE] Installation directory: $AceDir"

# Keep user PATH intact; refresh standard winget and uv install locations.
$env:Path = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Microsoft\WinGet\Links;C:\Program Files\Git\cmd;$env:Path"
foreach ($program in @('git', 'uv')) {
    if (-not (Get-Command $program -ErrorAction SilentlyContinue)) {
        throw "$program is missing. Run SETUP_ONLY.bat in Vocal Translate AI first."
    }
}

if (-not (Test-Path (Join-Path $AceDir 'pyproject.toml'))) {
    if (Test-Path $AceDir) {
        throw "ACE-Step directory exists without pyproject.toml: $AceDir. Set ACE_STEP_DIR to the correct installation."
    }
    Write-Host "[ACE] Cloning ACE-Step 1.5 from GitHub (first run)..."
    & git clone --depth 1 https://github.com/ace-step/ACE-Step-1.5.git "$AceDir"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clone ACE-Step into $AceDir."
    }
}

$AceVenv = Join-Path $AceDir '.venv\Scripts\python.exe'
Push-Location $AceDir
$TranscriptStarted = $false
try {
    try {
        Start-Transcript -Path (Join-Path $Logs 'ace_api.log') -Append | Out-Null
        $TranscriptStarted = $true
    } catch {
        Write-Warning "Could not create ACE API transcript log: $_"
    }
    if (-not (Test-Path $AceVenv)) {
        Write-Host "[ACE] Installing Python dependencies with uv sync..."
        & uv sync
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed. Read logs\ace_api.log."
        }
    }
    # REST API is local-only. Use DiT-only mode to conserve VRAM.
    $env:ACESTEP_API_HOST = '127.0.0.1'
    $env:ACESTEP_API_PORT = '8001'
    $env:ACESTEP_INIT_LLM = 'false'
    $env:ACESTEP_CONFIG_PATH = 'acestep-v15-turbo'
    $env:PYTHONUNBUFFERED = '1'
    Write-Host ""
    Write-Host "Starting ACE-Step 1.5 API (first startup downloads models)." -ForegroundColor Cyan
    Write-Host "Keep this window OPEN during ACE_ONLY.bat."
    Write-Host "After ACE_ONLY.bat finishes: press Ctrl+C here to free GPU VRAM."
    Write-Host "Then run FROM_ACE_GUIDE_ONLY.bat in Vocal Translate AI."
    Write-Host ""
    # Native child stdout/stderr are not reliably preserved by Start-Transcript
    # alone. Tee-Object makes the real ACE model download and startup messages
    # available in a standalone diagnostic file.
    $NativeLog = Join-Path $Logs 'ace_server.log'
    Write-Host "[ACE] Native server output: $NativeLog"
    & uv run --no-sync acestep-api 2>&1 |
        ForEach-Object { [string]$_ } |
        Tee-Object -FilePath $NativeLog -Append
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "ACE-Step API exited with code $exitCode. See logs\ace_server.log."
    }
} finally {
    if ($TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
    }
    Pop-Location
}
