# Signs both FrameAssistHost.exe builds with the self-signed code-signing
# certificate created by make-cert.ps1. Best-effort RFC3161 timestamping: a
# public timestamp server will usually refuse a self-signed chain, in which
# case the exes are signed without a timestamp (they expire with the cert, so
# keep the 10-year lookahead or re-sign before it lapses).
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$certPath = Join-Path $dir 'FrameAssistHost.p12'
$pwFile   = Join-Path $dir '.signing-password.txt'
if (-not (Test-Path $certPath)) { throw "Missing $certPath - run make-cert.ps1 first." }
if (-not (Test-Path $pwFile))   { throw "Missing $pwFile - run make-cert.ps1 first." }

$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2(
    $certPath, (Get-Content $pwFile -Raw).Trim())

$targets = @(
    (Join-Path $dir '..\..\dist\FrameAssistHost.exe'),          # one-file build
    (Join-Path $dir '..\..\dist\FrameAssistHost\FrameAssistHost.exe')  # onedir build
)

function Sign-One($path) {
    foreach ($ts in @('http://timestamp.digicert.com', $null)) {
        try {
            $arg = @{ FilePath = $path; Certificate = $cert; HashAlgorithm = 'SHA256' }
            if ($ts) { $arg.TimestampServer = $ts }
            Set-AuthenticodeSignature @arg | Out-Null
            return $(if ($ts) { 'signed+timestamped' } else { 'signed (no timestamp)' })
        } catch {
            if (-not $ts) { throw }
        }
    }
}

foreach ($t in $targets) {
    if (-not (Test-Path $t)) { Write-Warning "missing build, skipped: $t"; continue }
    $how = Sign-One $t
    $sig = Get-AuthenticodeSignature $t
    Write-Host "Signed $t" -ForegroundColor Green
    Write-Host ("  {0} | Signer: {1}" -f $how, $sig.SignerCertificate.Subject)
}
