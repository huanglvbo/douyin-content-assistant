[CmdletBinding()]
param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'CreatorNotes'))
$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$Marker = Join-Path $InstallRoot '.creator-notes-install.json'
$Python = Join-Path $InstallRoot 'runtime\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Marker) -or -not (Test-Path -LiteralPath $Python)) {
    throw 'No completed Douyin Content Assistant runtime was found here. Run the installer, or pass your -InstallRoot.'
}
$Ownership = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
if ($Ownership.project -ne 'creator-notes') { throw 'Installation ownership mismatch.' }
$env:CREATOR_NOTES_HOME = $InstallRoot
$env:CREATOR_NOTES_MODELS = Join-Path $InstallRoot 'models'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $InstallRoot 'browsers'
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = ''
& $Python -m creator_notes.cli doctor
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'No cookies, environment dump, media URLs or account credentials were printed.'
Write-Host 'A healthy runtime check does not prove WorkBuddy connector loading or Douyin access.'
