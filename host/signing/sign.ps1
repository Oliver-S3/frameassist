# Signs both FrameAssistHost.exe builds with the self-signed code-signing
# certificate created by make-cert.ps1.
#
# Uses signtool (Windows SDK) to DUAL-SIGN - a SHA-256 primary signature plus an
# appended SHA-1 signature - mirroring what electron-builder produces for the
# Quicksilver exe. Verified side-by-side: a SHA-256-only signature is atypical
# and is what SmartScreen classifies as an "unrecognized app"; the dual-signed
# + timestamped profile matches normal software and clears it after the
# one-time "Run anyway". Also stamps with a best-effort RFC3161 timestamp.
#
# NOTE: re-signing changes the file's SHA-256, which invalidates SmartScreen's
# remembered "Run anyway". Freeze the artifact after signing (build once, sign
# once, ship).
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$certPath = Join-Path $dir 'FrameAssistHost.p12'
$pwFile   = Join-Path $dir '.signing-password.txt'
if (-not (Test-Path $certPath)) { throw "Missing $certPath - run make-cert.ps1 first." }
if (-not (Test-Path $pwFile))   { throw "Missing $pwFile - run make-cert.ps1 first." }
$pass = (Get-Content $pwFile -Raw).Trim()
$ts = 'http://timestamp.digicert.com'

function Find-Signtool {
  foreach ($root in @('C:\Program Files (x86)\Windows Kits\10\bin',
                      'C:\Program Files\Windows Kits\10\bin')) {
    if (Test-Path $root) {
      $hit = Get-ChildItem -Path $root -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
             Where-Object { $_.FullName -like '*x64*' } | Select-Object -First 1
      if ($hit) { return $hit.FullName }
    }
  }
  $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  throw 'signtool not found - install the Windows SDK (Windows Kits) or add signtool to PATH.'
}
$sig = Find-Signtool
Write-Host "using signtool: $sig"

$targets = @(
    (Join-Path $dir '..\..\dist\FrameAssistHost.exe'),                 # one-file
    (Join-Path $dir '..\..\dist\FrameAssistHost\FrameAssistHost.exe')  # onedir
)

foreach ($t in $targets) {
  if (-not (Test-Path $t)) { Write-Warning "missing build, skipped: $t"; continue }

  # 1) Primary: SHA-256 + best-effort RFC3161 timestamp
  try {
    & $sig sign /f $certPath /p $pass /fd SHA256 /tr $ts /td SHA256 '/d' 'FrameAssistHost' $t | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "sha256 sign exit $LASTEXITCODE" }
  } catch {
    Write-Warning "timestamp failed, signing sha256 without one"
    & $sig sign /f $certPath /p $pass /fd SHA256 '/d' 'FrameAssistHost' $t | Out-Host
  }

  # 2) Append the SHA-1 signature (dual-signing, matches Quicksilver)
  & $sig sign /as /f $certPath /p $pass /fd SHA1 '/d' 'FrameAssistHost' $t | Out-Host

  # /all is required: without it signtool only reports the primary signature and
  # hides the appended SHA-1 one.
  $report = & $sig verify /pa /all /v $t 2>&1 | Out-String
  $hasSha1 = $report -match '\(sha1\)'
  $hasSha256 = $report -match '\(sha256\)'
  $tsc = $report -match 'Timestamp'
  Write-Host ("Signed {0}  sha256={1} sha1={2} timeconf={3}" -f $t,
              $hasSha256, $hasSha1, $tsc) -ForegroundColor Green
}
Write-Host 'DONE. This artifact is now frozen for release - do not re-sign/rebuild it.'
