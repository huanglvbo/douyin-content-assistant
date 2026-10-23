[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'CreatorNotes'),
    [switch]$RegisterWorkBuddy,
    [switch]$PrepareModel,
    [switch]$SkipModelDownload
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell's per-buffer web progress rendering is slow. Each stage
# prints its own progress, while uv and Playwright retain their native output.
$ProgressPreference = 'SilentlyContinue'
$SourceRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$UvVersion = '0.13.4'
$UvSha256 = 'ccec7508a07d9933d7cb1261880633efcf1f30d6e6eddab21823f1dae6f23225'
$UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
$script:InstallStages = New-Object 'System.Collections.Generic.List[object]'
$script:CurrentStage = 'Initialization'
$script:ModelStatus = 'deferred_until_first_transcription'
$StartedAt = (Get-Date).ToUniversalTime().ToString('o')
$TotalTimer = [Diagnostics.Stopwatch]::StartNew()

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE. Rerun setup to resume completed stages." }
}

function Assert-PlainDirectory {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        $Item = Get-Item -LiteralPath $Path -Force
        if (-not $Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Installation directory must be an ordinary directory: $Path"
        }
    }
}

function Write-LocalJson {
    param([string]$Path, [object]$Value)
    if (Test-Path -LiteralPath $Path) {
        $Item = Get-Item -LiteralPath $Path -Force
        if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Installer receipt may not be a link.' }
    }
    $Temporary = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($Temporary, ($Value | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $Temporary -Destination $Path -Force
}

function Invoke-Stage {
    param([string]$Name, [scriptblock]$Action)
    $script:CurrentStage = $Name
    $Timer = [Diagnostics.Stopwatch]::StartNew()
    Write-Host ''
    Write-Host ('=> ' + $Name)
    try {
        & $Action
        $script:InstallStages.Add(@{ stage=$Name; status='complete'; seconds=[Math]::Round($Timer.Elapsed.TotalSeconds,2) })
        Write-Host ('   Completed in ' + [Math]::Round($Timer.Elapsed.TotalSeconds,1) + ' seconds.')
    } catch {
        $script:InstallStages.Add(@{ stage=$Name; status='failed'; seconds=[Math]::Round($Timer.Elapsed.TotalSeconds,2) })
        throw
    }
}

function Read-InstallPlan {
    $Result = & $Python $StateScript inspect --source $SourceRoot --install-root $InstallRoot --python $Python
    if ($LASTEXITCODE -ne 0) { throw 'The cached installation could not be inspected. No completed-stage receipt was replaced.' }
    return ($Result | ConvertFrom-Json)
}

if ($PrepareModel -and $SkipModelDownload) { throw 'Choose -PrepareModel or -SkipModelDownload, not both.' }
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    throw 'This installer currently supports Windows x64 only. ARM64 requires separate acceptance.'
}
if ($InstallRoot -eq [IO.Path]::GetPathRoot($InstallRoot) -or $InstallRoot -eq $SourceRoot) {
    throw 'Choose a dedicated installation directory, separate from the downloaded source folder.'
}
Assert-PlainDirectory $InstallRoot
$Marker = Join-Path $InstallRoot '.creator-notes-install.json'
if (Test-Path -LiteralPath $InstallRoot) {
    $Existing = @(Get-ChildItem -LiteralPath $InstallRoot -Force)
    if ($Existing.Count -gt 0 -and -not (Test-Path -LiteralPath $Marker)) {
        throw 'The target directory is not an owned Douyin Content Assistant installation. Choose another -InstallRoot.'
    }
    if (Test-Path -LiteralPath $Marker) {
        $Ownership = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
        if ($Ownership.project -ne 'creator-notes') { throw 'Installation ownership mismatch.' }
    }
}
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $Marker)) { Write-LocalJson $Marker @{ project='creator-notes'; format=1 } }
try {
    $InstallLock = [IO.File]::Open((Join-Path $InstallRoot '.installer.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
} catch { throw 'Another installer is using this location. Let it finish before running setup again.' }

$ResultStatus = 'failed'
try {
    $Bootstrap = Join-Path $InstallRoot 'bootstrap'
    $Runtime = Join-Path $InstallRoot 'runtime'
    $ModelPath = Join-Path $InstallRoot 'models'
    $BrowserPath = Join-Path $InstallRoot 'browsers'
    $UvCache = Join-Path $InstallRoot 'cache\uv'
    $Integration = Join-Path $InstallRoot 'integration'
    foreach ($Directory in @($Bootstrap,$Runtime,$ModelPath,$BrowserPath,$UvCache,$Integration)) {
        Assert-PlainDirectory $Directory
        New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    }
    $Archive = Join-Path $Bootstrap "uv-$UvVersion-windows-x64.zip"
    $Uv = Join-Path $Bootstrap "uv-$UvVersion.exe"
    $StateScript = Join-Path $PSScriptRoot 'install_state.py'
    $Venv = Join-Path $Runtime '.venv'
    $Python = Join-Path $Venv 'Scripts\python.exe'
    $RuntimeReceipt = Join-Path $Integration 'runtime-python.json'
    if (Test-Path -LiteralPath $RuntimeReceipt) {
        $Selection = Get-Content -LiteralPath $RuntimeReceipt -Raw | ConvertFrom-Json
        $Candidate = [IO.Path]::GetFullPath($Selection.python)
        $RuntimePrefix = $Runtime.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        if ($Selection.project -ne 'creator-notes' -or -not $Candidate.StartsWith($RuntimePrefix,[StringComparison]::OrdinalIgnoreCase)) {
            throw 'Cached interpreter ownership mismatch.'
        }
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) { $Python = $Candidate }
    }

    # Environment changes remain local to setup and its child processes.
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtime 'python'
    $env:UV_CACHE_DIR = $UvCache
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    $env:UV_NO_PROGRESS = '0'
    $env:PLAYWRIGHT_BROWSERS_PATH = $BrowserPath
    $env:CREATOR_NOTES_HOME = $InstallRoot
    $env:CREATOR_NOTES_MODELS = $ModelPath
    $env:HF_HUB_DISABLE_TELEMETRY = '1'
    $env:HF_HUB_DISABLE_XET = '1'
    $env:PYTHONPATH = ''
    $env:PYTHONUTF8 = '1'

    Invoke-Stage 'Inspect local cache' {
        $script:Plan = [PSCustomObject]@{ runtime_ready=$false; package_current=$false; browser_ready=$false; app_path=$null }
        if (Test-Path -LiteralPath $Python -PathType Leaf) { $script:Plan = Read-InstallPlan }
        if ($Plan.package_current) { Write-Host '   Source fingerprint and installed dependencies match. Package reinstall will be skipped.' }
    }
    Invoke-Stage 'Prepare the verified bootstrap' {
        if ($Plan.package_current) {
            Write-Host '   Cached application is current; bootstrap and Python download checks skipped.'
            return
        }
        if (-not (Test-Path -LiteralPath $Archive) -or (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $UvSha256) {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            for ($Attempt=1; $Attempt -le 3; $Attempt++) {
                try {
                    Write-Host "   Downloading the pinned bootstrap (attempt $Attempt of 3)..."
                    $TemporaryArchive = Join-Path $Bootstrap ("download-" + [Guid]::NewGuid().ToString('N') + '.zip')
                    Invoke-WebRequest -Uri $UvUrl -OutFile $TemporaryArchive -UseBasicParsing -TimeoutSec 180
                    if ((Get-FileHash -LiteralPath $TemporaryArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $UvSha256) { throw 'Bootstrap SHA-256 verification failed.' }
                    Move-Item -LiteralPath $TemporaryArchive -Destination $Archive -Force
                    break
                } catch { if ($Attempt -eq 3) { throw }; Write-Host '   Download failed; retrying the bootstrap.' }
            }
        } else { Write-Host '   Reusing the verified bootstrap archive.' }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
        try {
            $Entries = @($Zip.Entries | Where-Object { $_.FullName -eq 'uv.exe' -or $_.FullName.EndsWith('/uv.exe') })
            if ($Entries.Count -ne 1) { throw 'Unexpected bootstrap archive layout.' }
            $InputStream = $Entries[0].Open()
            $OutputStream = [IO.File]::Create($Uv)
            try { $InputStream.CopyTo($OutputStream) } finally { $OutputStream.Dispose(); $InputStream.Dispose() }
        } finally { $Zip.Dispose() }
    }
    Invoke-Stage 'Prepare isolated Python 3.12' {
        if ($Plan.runtime_ready) { Write-Host '   Existing Python is healthy; runtime installation skipped.'; return }
        Invoke-Checked $Uv @('python','install','3.12')
        if (Test-Path -LiteralPath $Venv) { $Venv = Join-Path $Runtime ('.venv-' + [Guid]::NewGuid().ToString('N').Substring(0,8)) }
        Invoke-Checked $Uv @('venv','--python','3.12',$Venv)
        $script:Python = Join-Path $Venv 'Scripts\python.exe'
        Invoke-Checked $Python @('-I','-c','import sys; assert sys.version_info[:2] == (3,12)')
        Write-LocalJson $RuntimeReceipt @{ project='creator-notes'; python=$Python }
    }
    Invoke-Stage 'Install application and dependencies' {
        if ($Plan.package_current) { Write-Host '   Application source is unchanged; copying and package installation skipped.'; return }
        $SnapshotText = & $Python $StateScript stage --source $SourceRoot --install-root $InstallRoot
        if ($LASTEXITCODE -ne 0) { throw 'Could not stage a verified source snapshot.' }
        $Snapshot = $SnapshotText | ConvertFrom-Json
        if ($Snapshot.reused) { Write-Host '   Reusing the previous verified source snapshot.' }
        Invoke-Checked $Uv @('pip','install','--python',$Python,$Snapshot.app_path)
        $Recorded = & $Python $StateScript record-package --source $SourceRoot --install-root $InstallRoot --python $Python
        if ($LASTEXITCODE -ne 0) { throw 'Installed dependencies did not pass their local health check.' }
    }
    $script:Plan = Read-InstallPlan
    Invoke-Stage 'Prepare the Chromium browser' {
        if ($Plan.browser_ready) { Write-Host '   The required full Chromium build is cached; browser download skipped.'; return }
        Write-Host '   Downloading full Chromium for normal login and verification. The duplicate headless shell is excluded.'
        Invoke-Checked $Python @('-m','playwright','install','--no-shell','chromium')
    }
    Invoke-Stage 'Speech model preparation' {
        if ($PrepareModel) {
            Write-Host '   Preparing the small speech model. This one-time download may take several minutes.'
            Invoke-Checked $Python @('-m','creator_notes.cli','prepare-model','--model','small')
            $script:ModelStatus = 'prefetched'
        } else {
            Write-Host '   Deferred: setup does not download a speech model.'
            Write-Host '   First transcription will download its model if it is not cached, then run locally.'
            Write-Host '   Use -PrepareModel if you prefer to pay this download cost during setup.'
        }
    }
    Invoke-Stage 'Generate host files and check readiness' {
        $Configuration = Join-Path $PSScriptRoot 'configure.py'
        $ConfigureArgs = @($Configuration,'--python',$Python,'--install-root',$InstallRoot,'--skill-source',(Join-Path $SourceRoot 'skills\creator-notes'))
        if ($RegisterWorkBuddy) { $ConfigureArgs += '--register-workbuddy' }
        Invoke-Checked $Python $ConfigureArgs
        $DoctorText = & $Python -m creator_notes.cli doctor
        if ($LASTEXITCODE -ne 0) { throw 'Runtime diagnostics failed. Run scripts/diagnose.cmd for a local health report.' }
        $Doctor = $DoctorText | ConvertFrom-Json
        if (-not $Doctor.ready_for_login) { throw 'Runtime is not ready for login. Run scripts/diagnose.cmd before retrying.' }
        Write-Host '   Runtime and browser are ready for login. Model availability is checked on first use.'
    }
    $ResultStatus = 'ready_for_login'
    Write-Host ''
    Write-Host ('Setup completed in ' + [Math]::Round($TotalTimer.Elapsed.TotalSeconds,1) + ' seconds.')
    Write-Host ('MCP configuration: ' + (Join-Path $Integration 'workbuddy-mcp.json'))
    Write-Host ('Skill ZIP: ' + (Join-Path $Integration 'creator-notes-skill.zip'))
    if ($RegisterWorkBuddy) { Write-Host 'Enable/reconnect the owned connector and Skill in WorkBuddy.' }
    else { Write-Host 'Import the generated custom MCP configuration and upload the Skill ZIP in WorkBuddy.' }
    Write-Host 'Then check a creator and complete the first Douyin QR login yourself.'
    Write-Host 'WorkBuddy client acceptance remains a separate step.'
} finally {
    try {
        if (Test-Path -LiteralPath (Join-Path $InstallRoot 'integration') -PathType Container) {
            Write-LocalJson (Join-Path $InstallRoot 'integration\last-install.json') @{
                project='creator-notes'; started_at=$StartedAt; status=$ResultStatus;
                last_stage=$script:CurrentStage; seconds=[Math]::Round($TotalTimer.Elapsed.TotalSeconds,2);
                model_status=$script:ModelStatus; stages=@($script:InstallStages.ToArray())
            }
            Write-Host ('Stage timings: ' + (Join-Path $InstallRoot 'integration\last-install.json'))
        }
    } catch { Write-Host 'Could not save stage timings. Runtime and application data were retained.' }
    finally { $InstallLock.Dispose() }
}
