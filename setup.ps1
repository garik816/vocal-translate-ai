param(
    [switch]$ForceRepair
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root 'runtime'
$Logs = Join-Path $Root 'logs'
$Ace = Join-Path $Runtime 'ACE-Step-1.5'
$Seed = Join-Path $Runtime 'seed-vc'
$SeedReq = Join-Path $Root 'seedvc_inference_requirements.txt'

New-Item -ItemType Directory -Force -Path $Runtime, $Logs | Out-Null
$TranscriptStarted = $false
try {
    Start-Transcript -Path (Join-Path $Logs 'setup.log') -Append -ErrorAction Stop | Out-Null
    $TranscriptStarted = $true
} catch {
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $extra = @(
        "$env:USERPROFILE\.local\bin",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links",
        'C:\Program Files\Git\cmd'
    ) -join ';'
    $env:Path = "$machine;$user;$extra;$env:Path"
}

function Has-Command([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-UvArgs([string[]]$UvArgs, [int]$Retries = 3) {
    if (-not $UvArgs -or $UvArgs.Count -eq 0) {
        throw 'Internal error: Invoke-UvArgs received an empty argument list.'
    }
    for ($i = 1; $i -le $Retries; $i++) {
        Write-Host "uv $($UvArgs -join ' ')"
        & uv @UvArgs
        if ($LASTEXITCODE -eq 0) {
            return
        }
        if ($i -lt $Retries) {
            Write-Warning "uv command failed (attempt $i/$Retries). Retrying..."
            Start-Sleep -Seconds (3 * $i)
        }
    }
    throw "uv command failed after $Retries attempts: uv $($UvArgs -join ' ')"
}

function Try-UvArgs([string[]]$UvArgs) {
    if (-not $UvArgs -or $UvArgs.Count -eq 0) {
        Write-Warning 'Try-UvArgs received an empty argument list.'
        return $false
    }
    Write-Host "uv $($UvArgs -join ' ')"
    & uv @UvArgs
    return ($LASTEXITCODE -eq 0)
}

function Test-Python([string]$Python, [string]$Code) {
    if (-not (Test-Path $Python)) {
        return $false
    }
    & $Python -c $Code
    return ($LASTEXITCODE -eq 0)
}

function Ensure-WingetPackage([string]$CommandName, [string]$PackageId) {
    if (Has-Command $CommandName) {
        return
    }
    if (-not (Has-Command 'winget')) {
        throw "$CommandName is missing and winget is unavailable. Install $CommandName manually, then run SETUP_ONLY.bat."
    }
    Write-Host "Installing $PackageId..."
    & winget install --id $PackageId -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install $PackageId"
    }
    Refresh-Path
    if (-not (Has-Command $CommandName)) {
        throw "$PackageId was installed but $CommandName is still not visible in PATH. Restart RUN.bat."
    }
}

try {
    Write-Host '=== Vocal Translate AI v4: setup / verify ==='
    Refresh-Path
    $env:UV_HTTP_TIMEOUT = '180'
    $env:UV_HTTP_RETRIES = '5'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    Ensure-WingetPackage 'git' 'Git.Git'
    Ensure-WingetPackage 'uv' 'astral-sh.uv'
    Ensure-WingetPackage 'ffmpeg' 'Gyan.FFmpeg'

    # Detect GPU before choosing PyTorch builds.
    $GpuName = 'unknown'
    $GpuMemoryMb = 0
    if (Has-Command 'nvidia-smi') {
        try {
            $gpuLine = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
            if ($gpuLine) {
                $parts = $gpuLine -split ','
                $GpuName = $parts[0].Trim()
                if ($parts.Count -gt 1) {
                    [int]::TryParse($parts[1].Trim(), [ref]$GpuMemoryMb) | Out-Null
                }
            }
        } catch {
            Write-Warning "GPU detection failed: $($_.Exception.Message)"
        }
    }

    $LegacyGtx970 = $GpuName -match 'GTX\s*970'
    $Rtx3080 = $GpuName -match 'RTX\s*3080'
    $LowVram = ($GpuMemoryMb -gt 0 -and $GpuMemoryMb -le 4608)
    $GpuProfileName = if ($LegacyGtx970) {
        'legacy_maxwell_4gb'
    } elseif ($Rtx3080) {
        'ampere_rtx3080'
    } elseif ($LowVram) {
        'low_vram'
    } else {
        'modern'
    }

    $GpuProfile = [ordered]@{
        profile = $GpuProfileName
        name = $GpuName
        memory_mb = $GpuMemoryMb
        legacy_torch = [bool]$LegacyGtx970
        low_vram = [bool]$LowVram
        rtx3080 = [bool]$Rtx3080
        release_between_stages = [bool]($Rtx3080 -or $LowVram)
    }
    $GpuProfile | ConvertTo-Json | Set-Content -Path (Join-Path $Runtime 'gpu_profile.json') -Encoding UTF8
    Write-Host "[GPU] $GpuName / $GpuMemoryMb MB / profile=$GpuProfileName"

    if (-not (Test-Path (Join-Path $Ace '.git'))) {
        Write-Host '[ACE-Step] Cloning repository...'
        & git clone https://github.com/ACE-Step/ACE-Step-1.5.git $Ace
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to clone ACE-Step.'
        }
    } else {
        Write-Host '[ACE-Step] Existing repository found. No automatic git pull.'
    }

    $AcePython = Join-Path $Ace '.venv\Scripts\python.exe'
    if ($LegacyGtx970) {
        $AceOk = Test-Python $AcePython "import torch,acestep; print('ACE torch:',torch.__version__); print('ACE CUDA:',torch.cuda.is_available())"
    } elseif ($Rtx3080) {
        $AceOk = Test-Python $AcePython "import torch,acestep; print('ACE torch:',torch.__version__); print('ACE CUDA build:',torch.version.cuda); assert torch.cuda.is_available(); assert torch.cuda.get_device_capability(0) == (8, 6); assert 'sm_86' in torch.cuda.get_arch_list(); assert torch.__version__.startswith('2.7.1'); assert torch.version.cuda == '12.8'"
    } else {
        $AceOk = Test-Python $AcePython "import torch,acestep; print('ACE torch:',torch.__version__); print('ACE CUDA:',torch.cuda.is_available()); cap=torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None; arch=(f'sm_{cap[0]}{cap[1]}' if cap else None); print('ACE device arch:',arch); print('ACE wheel arches:',torch.cuda.get_arch_list() if torch.cuda.is_available() else []); assert (not torch.cuda.is_available()) or arch in torch.cuda.get_arch_list()"
    }
    if ($ForceRepair -or -not $AceOk) {
        Write-Host '[ACE-Step] Creating/repairing environment WITHOUT flash-attn...'
        Push-Location $Ace
        try {
            Invoke-UvArgs @('sync', '--frozen', '--no-install-package', 'flash-attn') 3
        } finally {
            Pop-Location
        }
    } else {
        Write-Host '[ACE-Step] Environment OK. Skipping uv sync.'
    }

    if ($LegacyGtx970) {
        $AceLegacyOk = Test-Python $AcePython "import torch; print('ACE capability:', torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None); print('ACE arches:', torch.cuda.get_arch_list() if torch.cuda.is_available() else []); assert torch.cuda.is_available(); assert torch.cuda.get_device_capability(0) == (5, 2); assert 'sm_52' in torch.cuda.get_arch_list()"
        if ($ForceRepair -or -not $AceLegacyOk) {
            Write-Host '[ACE-Step] GTX 970 detected: installing legacy CUDA 12.1 PyTorch with Maxwell support...'
            Invoke-UvArgs @(
                'pip', 'install', '--python', $AcePython, '--force-reinstall',
                '--index-url', 'https://download.pytorch.org/whl/cu121',
                'torch==2.5.1+cu121', 'torchvision==0.20.1+cu121', 'torchaudio==2.5.1+cu121'
            ) 4
            Write-Host '[ACE-Step] Installing legacy-compatible torchao for INT8 Tier-1 mode...'
            Invoke-UvArgs @(
                'pip', 'install', '--python', $AcePython, '--force-reinstall',
                'torchao==0.11.0'
            ) 4
        } else {
            Write-Host '[ACE-Step] GTX 970 legacy PyTorch is already compatible.'
        }
    }

    if (-not (Test-Python $AcePython "import demucs; print('Demucs OK')")) {
        Write-Host '[Demucs] Missing. Trying uv cache first...'
        $offline = Try-UvArgs @('pip', 'install', '--offline', '--python', $AcePython, 'demucs==4.0.1')
        if (-not $offline) {
            Write-Host '[Demucs] Not in local cache. Installing from PyPI...'
            Invoke-UvArgs @('pip', 'install', '--python', $AcePython, 'demucs==4.0.1') 4
        }
    } else {
        Write-Host '[Demucs] Already installed.'
    }

    if (-not (Test-Path (Join-Path $Seed '.git'))) {
        Write-Host '[Seed-VC] Cloning repository...'
        & git clone https://github.com/Plachtaa/seed-vc.git $Seed
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to clone Seed-VC.'
        }
    } else {
        Write-Host '[Seed-VC] Existing repository found. No automatic git pull.'
    }

    $SeedPython = Join-Path $Seed '.venv\Scripts\python.exe'
    if (-not (Test-Path $SeedPython)) {
        Write-Host '[Seed-VC] Creating Python 3.10 environment...'
        & uv venv (Join-Path $Seed '.venv') --python 3.10
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to create Seed-VC venv.'
        }
    }

    if ($LegacyGtx970) {
        $SeedTorchOk = Test-Python $SeedPython "import torch,torchaudio; print('Seed torch:', torch.__version__); print('Seed CUDA:', torch.version.cuda); print('Seed capability:', torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None); print('Seed arches:', torch.cuda.get_arch_list() if torch.cuda.is_available() else []); assert torch.cuda.is_available(); assert torch.cuda.get_device_capability(0) == (5, 2); assert 'sm_52' in torch.cuda.get_arch_list()"
        if ($ForceRepair -or -not $SeedTorchOk) {
            Write-Host '[Seed-VC] GTX 970 detected: installing CUDA 12.1 legacy PyTorch...'
            $legacySeedArgs = @(
                'pip', 'install', '--python', $SeedPython, '--force-reinstall',
                '--index-url', 'https://download.pytorch.org/whl/cu121',
                'torch==2.5.1+cu121', 'torchvision==0.20.1+cu121', 'torchaudio==2.5.1+cu121'
            )
            Invoke-UvArgs $legacySeedArgs 4
        } else {
            Write-Host '[Seed-VC] GTX 970 legacy PyTorch already installed.'
        }
    } else {
        if ($Rtx3080) {
            $SeedTorchOk = Test-Python $SeedPython "import torch,torchaudio; print('Seed torch:',torch.__version__); print('Seed CUDA build:',torch.version.cuda); assert torch.cuda.is_available(); assert torch.cuda.get_device_capability(0) == (8, 6); assert 'sm_86' in torch.cuda.get_arch_list(); assert torch.__version__.startswith('2.7.1'); assert torch.version.cuda == '12.8'"
        } else {
            $SeedTorchOk = Test-Python $SeedPython "import torch,torchaudio; print('Seed torch:',torch.__version__); print('Seed CUDA build:',torch.version.cuda); assert torch.version.cuda is not None; cap=torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None; arch=(f'sm_{cap[0]}{cap[1]}' if cap else None); print('Seed device arch:',arch); print('Seed wheel arches:',torch.cuda.get_arch_list() if torch.cuda.is_available() else []); assert torch.cuda.is_available(); assert arch in torch.cuda.get_arch_list()"
        }
        if ($ForceRepair -or -not $SeedTorchOk) {
            Write-Host '[Seed-VC] Installing CUDA 12.8 PyTorch. Trying uv cache first...'
            $torchArgs = @(
                'pip', 'install', '--python', $SeedPython,
                '--index-url', 'https://download.pytorch.org/whl/cu128',
                'torch==2.7.1+cu128', 'torchvision==0.22.1+cu128', 'torchaudio==2.7.1+cu128'
            )
            $offlineTorch = @('pip', 'install', '--offline', '--python', $SeedPython, '--index-url', 'https://download.pytorch.org/whl/cu128', 'torch==2.7.1+cu128', 'torchvision==0.22.1+cu128', 'torchaudio==2.7.1+cu128')
            if (-not (Try-UvArgs $offlineTorch)) {
                Invoke-UvArgs $torchArgs 4
            }
        } else {
            Write-Host '[Seed-VC] PyTorch already installed. No download.'
        }
    }

    $SeedImports = "import numpy, scipy, librosa, huggingface_hub, munch, einops, transformers, soundfile, yaml, dac; from dac.nn.quantize import VectorQuantize; print('Seed inference dependencies OK')"
    $SeedDepsOk = Test-Python $SeedPython $SeedImports
    if ($ForceRepair -or -not $SeedDepsOk) {
        Write-Host '[Seed-VC] Installing minimal inference dependencies. Trying uv cache first...'
        $offlineDeps = @('pip', 'install', '--offline', '--python', $SeedPython, '-r', $SeedReq)
        if (-not (Try-UvArgs $offlineDeps)) {
            Invoke-UvArgs @('pip', 'install', '--python', $SeedPython, '-r', $SeedReq) 4
        }
    } else {
        Write-Host '[Seed-VC] Inference dependencies already installed.'
    }

    if (-not (Test-Python $SeedPython $SeedImports)) {
        throw 'Seed-VC dependency verification failed. See logs/setup.log.'
    }

    if (-not (Test-Path (Join-Path $Seed 'inference.py'))) {
        throw 'Seed-VC inference.py is missing.'
    }

    Set-Content -Path (Join-Path $Runtime '.last_setup_ok') -Value (Get-Date -Format o) -Encoding ascii
    Write-Host ''
    Write-Host 'SETUP / VERIFY COMPLETE.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host ''
    Write-Host "SETUP ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'See logs\setup.log for details.' -ForegroundColor Yellow
    exit 1
} finally {
    if ($TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
    }
}
