[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'CreatorNotes'),
    [switch]$RegisterWorkBuddy,
    [switch]$SkipModelDownload
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'
$SourceRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$UvVersion = '0.13.4'
$UvSha256 = 'ccec7508a07d9933d7cb1261880633efcf1f30d6e6eddab21823f1dae6f23225'
$UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE. Run this installer again to retry." }
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
@{ project='creator-notes'; format=1 } | ConvertTo-Json | Set-Content -LiteralPath $Marker -Encoding UTF8

$Bootstrap = Join-Path $InstallRoot 'bootstrap'
$Runtime = Join-Path $InstallRoot 'runtime'
$ModelPath = Join-Path $InstallRoot 'models'
$BrowserPath = Join-Path $InstallRoot 'browsers'
$UvCache = Join-Path $InstallRoot 'cache\uv'
foreach ($Directory in @($Bootstrap,$Runtime,$ModelPath,$BrowserPath,$UvCache)) {
    Assert-PlainDirectory $Directory
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
}
$Archive = Join-Path $Bootstrap "uv-$UvVersion-windows-x64.zip"
$Uv = Join-Path $Bootstrap "uv-$UvVersion.exe"

Write-Host '[1/6] Preparing a verified private Python installer...'
if (-not (Test-Path -LiteralPath $Archive) -or (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $UvSha256) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $Downloaded = $false
    for ($Attempt=1; $Attempt -le 3; $Attempt++) {
        try {
            $TemporaryArchive = Join-Path $Bootstrap ("download-" + [Guid]::NewGuid().ToString('N') + '.zip')
            Invoke-WebRequest -Uri $UvUrl -OutFile $TemporaryArchive -UseBasicParsing -TimeoutSec 180
            if ((Get-FileHash -LiteralPath $TemporaryArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $UvSha256) {
                throw 'The downloaded bootstrap archive failed SHA-256 verification.'
            }
            Move-Item -LiteralPath $TemporaryArchive -Destination $Archive -Force
            $Downloaded = $true
            break
        } catch {
            if ($Attempt -eq 3) { throw }
            Write-Host "Download attempt $Attempt failed; retrying..."
        }
    }
    if (-not $Downloaded) { throw 'Could not download the verified bootstrap.' }
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Zip = [IO.Compression.ZipFile]::OpenRead($Archive)
try {
    $Entries = @($Zip.Entries | Where-Object { $_.FullName -eq 'uv.exe' -or $_.FullName.EndsWith('/uv.exe') })
    if ($Entries.Count -ne 1) { throw 'Unexpected bootstrap archive layout.' }
    $InputStream = $Entries[0].Open()
    $OutputStream = [IO.File]::Create($Uv)
    try { $InputStream.CopyTo($OutputStream) } finally { $OutputStream.Dispose(); $InputStream.Dispose() }
} finally { $Zip.Dispose() }

# Environment changes are confined to this installer process and its children.
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtime 'python'
$env:UV_CACHE_DIR = $UvCache
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:UV_NO_PROGRESS = '0'
$env:PLAYWRIGHT_BROWSERS_PATH = $BrowserPath
$env:CREATOR_NOTES_HOME = $InstallRoot
$env:CREATOR_NOTES_MODELS = $ModelPath
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:PYTHONPATH = ''
$env:PYTHONUTF8 = '1'

Write-Host '[2/6] Installing isolated Python 3.12 (no system Python required)...'
Invoke-Checked $Uv @('python','install','3.12')
$Venv = Join-Path $Runtime '.venv'
$Python = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { Invoke-Checked $Uv @('venv','--python','3.12',$Venv) }

Write-Host '[3/6] Installing the independent Douyin Content Assistant package...'
$ReleaseName = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$AppRoot = Join-Path $InstallRoot (Join-Path 'app' $ReleaseName)
New-Item -ItemType Directory -Path $AppRoot -Force | Out-Null
$CopyScript = Join-Path $PSScriptRoot 'copy_source.py'
Invoke-Checked $Python @($CopyScript,'--source',$SourceRoot,'--destination',$AppRoot)
Invoke-Checked $Uv @('pip','install','--python',$Python,'--reinstall-package','creator-notes',$AppRoot)

Write-Host '[4/6] Installing a private Chromium browser...'
Invoke-Checked $Python @('-m','playwright','install','chromium')
if (-not $SkipModelDownload) {
    Write-Host '[5/6] Downloading the local small speech model; this can take several minutes...'
    Invoke-Checked $Python @('-m','creator_notes.cli','prepare-model','--model','small')
} else {
    Write-Host '[5/6] Model download deferred; first transcription will download it.'
}

Write-Host '[6/6] Generating local WorkBuddy configuration and Skill...'
$Configuration = Join-Path $AppRoot 'scripts\configure.py'
$ConfigureArgs = @($Configuration,'--python',$Python,'--install-root',$InstallRoot,'--skill-source',(Join-Path $AppRoot 'skills\creator-notes'))
if ($RegisterWorkBuddy) { $ConfigureArgs += '--register-workbuddy' }
Invoke-Checked $Python $ConfigureArgs
Invoke-Checked $Python @('-m','creator_notes.cli','doctor')
Write-Host ''
Write-Host 'Installation files are ready. WorkBuddy client acceptance is a separate step.'
Write-Host ('MCP configuration: ' + (Join-Path $InstallRoot 'integration\workbuddy-mcp.json'))
Write-Host ('Skill ZIP: ' + (Join-Path $InstallRoot 'integration\creator-notes-skill.zip'))
if ($RegisterWorkBuddy) {
    Write-Host 'Registration was merged into .codebuddy with backups. Enable/reconnect it in WorkBuddy.'
} else {
    Write-Host 'In WorkBuddy, import the generated custom MCP configuration and upload the Skill ZIP.'
}
Write-Host 'Then ask WorkBuddy to check a creator. Complete the first Douyin QR login yourself.'
