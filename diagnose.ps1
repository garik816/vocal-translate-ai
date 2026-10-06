$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root 'logs'
$Log = Join-Path $LogDir 'diagnose.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
"=== Diagnose $(Get-Date -Format o) ===" | Set-Content $Log

function Add-Line([string]$Text) {
    $Text | Tee-Object -FilePath $Log -Append
}

Add-Line "PowerShell: $($PSVersionTable.PSVersion)"
foreach ($cmd in @('git','uv','ffmpeg','nvidia-smi')) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) { Add-Line "${cmd}: $($found.Source)" } else { Add-Line "${cmd}: MISSING" }
}

$AcePy = Join-Path $Root 'runtime\ACE-Step-1.5\.venv\Scripts\python.exe'
$SeedPy = Join-Path $Root 'runtime\seed-vc\.venv\Scripts\python.exe'
if (Test-Path $AcePy) {
    Add-Line '--- ACE Python ---'
    & $AcePy -c "import sys,torch; print(sys.version); print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO CUDA')" 2>&1 | Tee-Object -FilePath $Log -Append
    & $AcePy -c "import demucs; print('demucs import OK')" 2>&1 | Tee-Object -FilePath $Log -Append
} else {
    Add-Line 'ACE Python: MISSING'
}
if (Test-Path $SeedPy) {
    Add-Line '--- Seed Python ---'
    & $SeedPy -c "import sys,torch; print(sys.version); print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO CUDA')" 2>&1 | Tee-Object -FilePath $Log -Append
    & $SeedPy -c "import numpy,scipy,librosa,huggingface_hub,munch,einops,transformers,soundfile,yaml; print('seed imports OK')" 2>&1 | Tee-Object -FilePath $Log -Append
} else {
    Add-Line 'Seed Python: MISSING'
}
Add-Line "Saved: $Log"
