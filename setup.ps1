param(
    [switch]$ForceRepair
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runtime = Join-Path $Root 'runtime'
$Logs = Join-Path $Root 'logs'
$Downloads = Join-Path $Runtime 'downloads'
$Seed = Join-Path $Runtime 'seed-vc'
$SeedReq = Join-Path $Root 'seedvc_inference_requirements.txt'
$OpenUtau = Join-Path $Runtime 'OpenUtau-lunai'
$DiffSingerRoot = Join-Path $Runtime 'diffsinger'
$HeadlessProject = Join-Path $Root 'tools\OpenUtauHeadless\OpenUtauHeadless.csproj'
$HeadlessDll = Join-Path $Root 'tools\OpenUtauHeadless\bin\Release\net10.0\OpenUtauHeadless.dll'

New-Item -ItemType Directory -Force -Path $Runtime, $Logs, $Downloads, $DiffSingerRoot | Out-Null

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
        'C:\Program Files\Git\cmd',
        'C:\Program Files\dotnet'
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
            Start-Sleep -Seconds (4 * $i)
        }
    }
    throw "uv command failed after $Retries attempts: uv $($UvArgs -join ' ')"
}

function Try-UvArgs([string[]]$UvArgs) {
    if (-not $UvArgs -or $UvArgs.Count -eq 0) {
        return $false
    }
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
        throw "$CommandName is missing and winget is unavailable. Install $CommandName manually."
    }
    Write-Host "Installing $PackageId..."
    & winget install --id $PackageId -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install $PackageId"
    }
    Refresh-Path
}

function Ensure-DotNet10 {
    $have10 = $false
    if (Has-Command 'dotnet') {
        try {
            $have10 = [bool]((& dotnet --list-sdks) | Select-String -Pattern '^10\.')
        } catch {
            $have10 = $false
        }
    }
    if ($have10) {
        return
    }
    if (-not (Has-Command 'winget')) {
        throw '.NET 10 SDK is required for the OpenUtau headless renderer.'
    }
    Write-Host 'Installing .NET 10 SDK...'
    & winget install --id Microsoft.DotNet.SDK.10 -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to install .NET 10 SDK.'
    }
    Refresh-Path
    $have10 = [bool]((& dotnet --list-sdks) | Select-String -Pattern '^10\.')
    if (-not $have10) {
        throw '.NET 10 SDK was installed but is not visible yet. Restart RUN.bat.'
    }
}

function Download-File([string]$Url, [string]$Destination) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    if (Has-Command 'curl.exe') {
        Write-Host "Downloading: $Url"
        & curl.exe -L --fail --retry 5 --retry-delay 5 -C - -o $Destination $Url
        if ($LASTEXITCODE -ne 0) {
            throw "Download failed: $Url"
        }
        return
    }
    Write-Host "Downloading with Invoke-WebRequest: $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
}

try {
    Write-Host '=== Vocal Translate AI v7.3.0: setup / verify ==='
    Refresh-Path

    $env:UV_HTTP_TIMEOUT = '180'
    $env:UV_HTTP_RETRIES = '5'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    Ensure-WingetPackage 'git' 'Git.Git'
    Ensure-WingetPackage 'uv' 'astral-sh.uv'
    Ensure-WingetPackage 'ffmpeg' 'Gyan.FFmpeg'
    Ensure-DotNet10

    # GPU detection.
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
    }
    $GpuProfile | ConvertTo-Json | Set-Content -Path (Join-Path $Runtime 'gpu_profile.json') -Encoding UTF8
    Write-Host "[GPU] $GpuName / $GpuMemoryMb MB / profile=$GpuProfileName"

    # Seed-VC is also the shared Python/CUDA environment for Demucs, Whisper and melody extraction.
    if (-not (Test-Path (Join-Path $Seed '.git'))) {
        Write-Host '[Seed-VC] Cloning repository...'
        & git clone --depth 1 https://github.com/Plachtaa/seed-vc.git $Seed
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to clone Seed-VC.'
        }
    } else {
        Write-Host '[Seed-VC] Existing repository found. No automatic git pull.'
    }

    $SeedPython = Join-Path $Seed '.venv\Scripts\python.exe'
    if (-not (Test-Path $SeedPython)) {
        Write-Host '[Python] Installing managed Python 3.10 and creating venv...'
        Invoke-UvArgs @('python', 'install', '3.10') 3
        & uv venv (Join-Path $Seed '.venv') --python 3.10
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to create Seed-VC venv.'
        }
    }

    if ($LegacyGtx970) {
        $TorchOk = Test-Python $SeedPython "import torch; assert torch.cuda.is_available(); assert torch.cuda.get_device_capability(0)==(5,2); assert 'sm_52' in torch.cuda.get_arch_list()"
        if ($ForceRepair -or -not $TorchOk) {
            Write-Host '[PyTorch] GTX 970: installing CUDA 12.1 / Maxwell-compatible build...'
            Invoke-UvArgs @(
                'pip', 'install', '--python', $SeedPython, '--force-reinstall',
                '--index-url', 'https://download.pytorch.org/whl/cu121',
                'torch==2.5.1+cu121', 'torchvision==0.20.1+cu121', 'torchaudio==2.5.1+cu121'
            ) 4
        }
    } else {
        $TorchOk = Test-Python $SeedPython "import torch; assert torch.cuda.is_available(); cap=torch.cuda.get_device_capability(0); arch=f'sm_{cap[0]}{cap[1]}'; assert arch in torch.cuda.get_arch_list(); assert torch.__version__.startswith('2.7.1'); assert torch.version.cuda=='12.8'"
        if ($ForceRepair -or -not $TorchOk) {
            Write-Host '[PyTorch] Installing CUDA 12.8 build...'
            $offline = Try-UvArgs @(
                'pip', 'install', '--offline', '--python', $SeedPython,
                '--index-url', 'https://download.pytorch.org/whl/cu128',
                'torch==2.7.1+cu128', 'torchvision==0.22.1+cu128', 'torchaudio==2.7.1+cu128'
            )
            if (-not $offline) {
                Invoke-UvArgs @(
                    'pip', 'install', '--python', $SeedPython,
                    '--index-url', 'https://download.pytorch.org/whl/cu128',
                    'torch==2.7.1+cu128', 'torchvision==0.22.1+cu128', 'torchaudio==2.7.1+cu128'
                ) 4
            }
        }
    }

    $RuntimeImports = "import numpy,scipy,librosa,huggingface_hub,munch,einops,transformers,soundfile,yaml,dac,demucs,whisper,mutagen; from dac.nn.quantize import VectorQuantize; print('v7 Python runtime OK')"
    $DepsOk = Test-Python $SeedPython $RuntimeImports
    if ($ForceRepair -or -not $DepsOk) {
        Write-Host '[Python] Installing/repairing v7 inference dependencies...'
        $offlineDeps = @('pip', 'install', '--offline', '--python', $SeedPython, '-r', $SeedReq)
        if (-not (Try-UvArgs $offlineDeps)) {
            Invoke-UvArgs @('pip', 'install', '--python', $SeedPython, '-r', $SeedReq) 4
        }
    }

    if (-not (Test-Python $SeedPython $RuntimeImports)) {
        throw 'Python runtime verification failed. See logs\setup.log.'
    }

    if (-not (Test-Path (Join-Path $Seed 'inference.py'))) {
        throw 'Seed-VC inference.py is missing.'
    }

    # OpenUtau-Lunai contains the Ukrainian DiffSinger phonemizer.
    if (-not (Test-Path (Join-Path $OpenUtau '.git'))) {
        Write-Host '[OpenUtau] Cloning OpenUtau-Lunai...'
        & git clone --depth 1 https://github.com/keirokeer/OpenUtau-lunai.git $OpenUtau
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to clone OpenUtau-Lunai.'
        }
    } else {
        Write-Host '[OpenUtau] Existing repository found. No automatic git pull.'
    }

    # Install a DiffSinger guide voice that explicitly supports Ukrainian.
    # Nero v170 loads correctly, but its phoneme inventory does not support the
    # Ukrainian phonemizer: all generated phonemes validate as errors. Amaboshi
    # Cipher v170 is multilingual and includes Ukrainian support.
    $VoiceName = 'Amaboshi Cipher'
    $VoiceHint = 'Amaboshi'
    $VoiceArchiveName = 'Amaboshi_Cipher_v170.zip'
    $VoiceFolderName = 'Amaboshi_Cipher_v170'
    $VoiceZip = Join-Path $Downloads $VoiceArchiveName
    $VoiceUrl = 'https://github.com/lunaiproject/lunai_singers/releases/download/170/Amaboshi_Cipher_v170.zip'
    $VoiceInstall = Join-Path $DiffSingerRoot $VoiceFolderName

    $VoiceCharacter = $null
    if (Test-Path $VoiceInstall) {
        $VoiceCharacter = Get-ChildItem -Path $VoiceInstall -Recurse -Filter 'character.txt' -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.DirectoryName 'dsconfig.yaml') } |
            Select-Object -First 1
    }

    if ($ForceRepair -or -not $VoiceCharacter) {
        if (-not (Test-Path $VoiceZip)) {
            Write-Host "[DiffSinger] Downloading Ukrainian-capable $VoiceName voicebank..."
            Download-File $VoiceUrl $VoiceZip
        } else {
            Write-Host "[DiffSinger] Reusing already downloaded $VoiceArchiveName."
        }

        if (Test-Path $VoiceInstall) {
            Remove-Item -LiteralPath $VoiceInstall -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path $VoiceInstall | Out-Null

        Write-Host "[DiffSinger] Installing $VoiceName into: $VoiceInstall"
        Expand-Archive -LiteralPath $VoiceZip -DestinationPath $VoiceInstall -Force

        $VoiceCharacter = Get-ChildItem -Path $VoiceInstall -Recurse -Filter 'character.txt' -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.DirectoryName 'dsconfig.yaml') } |
            Select-Object -First 1

        if (-not $VoiceCharacter) {
            throw "$VoiceName archive was extracted but no valid DiffSinger root (character.txt + dsconfig.yaml) was found."
        }
    }

    Write-Host "[DiffSinger] Voicebank ready: $($VoiceCharacter.DirectoryName)"
    Write-Host "[DiffSinger] character.txt: $($VoiceCharacter.FullName)"
    Write-Host "[DiffSinger] dsconfig.yaml: $(Join-Path $VoiceCharacter.DirectoryName 'dsconfig.yaml')"

    # Build the tiny console renderer against OpenUtau-Lunai Core.
    Write-Host '[OpenUtau] Building headless renderer...'
    & dotnet build $HeadlessProject -c Release --nologo
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $HeadlessDll)) {
        throw 'Failed to build OpenUtau headless renderer.'
    }

    # OpenUtau portable SDK builds keep runtime data next to the Release
    # configuration folder. Create Cache up front so DiffSinger tensor-cache
    # writes cannot fail on the first render.
    $HeadlessReleaseDir = Split-Path (Split-Path $HeadlessDll -Parent) -Parent
    $OpenUtauData = Join-Path $HeadlessReleaseDir 'OpenUtau-Lunai-Data'
    New-Item -ItemType Directory -Force -Path $OpenUtauData | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $OpenUtauData 'Cache') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $OpenUtauData 'Logs') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $OpenUtauData 'Singers') | Out-Null
    Write-Host "[OpenUtau] Headless cache ready: $(Join-Path $OpenUtauData 'Cache')"

    Write-Host '[OpenUtau] Probing Ukrainian DiffSinger runtime...'
    & dotnet $HeadlessDll --probe $DiffSingerRoot $VoiceHint
    if ($LASTEXITCODE -ne 0) {
        throw 'OpenUtau/DiffSinger probe failed. See logs\setup.log.'
    }

    Set-Content -Path (Join-Path $Runtime '.last_setup_ok') -Value (Get-Date -Format o) -Encoding ascii
    Write-Host ''
    Write-Host 'V7 SETUP / VERIFY COMPLETE.' -ForegroundColor Green
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
