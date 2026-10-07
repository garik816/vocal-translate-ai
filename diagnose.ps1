$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root 'logs'
$Log = Join-Path $LogDir 'diagnose.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
"=== Vocal Translate AI v7 Diagnose $(Get-Date -Format o) ===" | Set-Content $Log

function Add-Line([string]$Text) {
    $Text | Tee-Object -FilePath $Log -Append
}

Add-Line "PowerShell: $($PSVersionTable.PSVersion)"
$Profile = Join-Path $Root 'runtime\gpu_profile.json'
if (Test-Path $Profile) {
    Add-Line '--- GPU profile ---'
    Get-Content $Profile | Tee-Object -FilePath $Log -Append
}

foreach ($cmd in @('git','uv','ffmpeg','dotnet','nvidia-smi')) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        Add-Line "${cmd}: $($found.Source)"
    } else {
        Add-Line "${cmd}: MISSING"
    }
}

if (Get-Command dotnet -ErrorAction SilentlyContinue) {
    Add-Line '--- .NET SDKs ---'
    & dotnet --list-sdks 2>&1 | Tee-Object -FilePath $Log -Append
}

$SeedPy = Join-Path $Root 'runtime\seed-vc\.venv\Scripts\python.exe'
if (Test-Path $SeedPy) {
    Add-Line '--- v7 Python / CUDA ---'
    & $SeedPy -c "import sys,torch; print(sys.version); print('torch',torch.__version__); print('cuda build',torch.version.cuda); print('cuda available',torch.cuda.is_available()); print('gpu',torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO CUDA'); print('capability',torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None); print('arches',torch.cuda.get_arch_list() if torch.cuda.is_available() else [])" 2>&1 | Tee-Object -FilePath $Log -Append
    & $SeedPy -c "import numpy,scipy,librosa,yaml,dac,demucs,whisper,mutagen; print('v7 imports OK')" 2>&1 | Tee-Object -FilePath $Log -Append
} else {
    Add-Line 'v7 Python: MISSING'
}

$Headless = Join-Path $Root 'tools\OpenUtauHeadless\bin\Release\net10.0\OpenUtauHeadless.dll'
if (Test-Path $Headless) {
    Add-Line 'OpenUtau headless: OK'
    $SingerRootProbe = Join-Path $Root 'runtime\diffsinger'
    Add-Line '--- OpenUtau / DiffSinger probe ---'
    & dotnet $Headless --probe $SingerRootProbe 'Nero' 2>&1 | Tee-Object -FilePath $Log -Append
    Add-Line "OpenUtau probe exit code: $LASTEXITCODE"
} else {
    Add-Line 'OpenUtau headless: MISSING'
}

$OpenUtau = Join-Path $Root 'runtime\OpenUtau-lunai'
if (Test-Path (Join-Path $OpenUtau '.git')) {
    Add-Line 'OpenUtau-Lunai repo: OK'
} else {
    Add-Line 'OpenUtau-Lunai repo: MISSING'
}

$SingerRoot = Join-Path $Root 'runtime\diffsinger'
$DsConfig = Get-ChildItem -Path $SingerRoot -Recurse -Filter 'dsconfig.yaml' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($DsConfig) {
    Add-Line "DiffSinger voicebank: $($DsConfig.DirectoryName)"
} else {
    Add-Line 'DiffSinger voicebank: MISSING'
}

Add-Line "Saved: $Log"
