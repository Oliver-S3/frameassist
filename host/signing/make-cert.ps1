# Creates a fresh self-signed code-signing certificate for FrameAssistHost.exe.
#
# Produces (in this folder):
#   FrameAssistHost.cer            - the PUBLIC certificate (safe to share/commit)
#   FrameAssistHost.p12            - the PRIVATE key, password-protected (gitignored!)
#   .signing-password.txt          - the p12 password (gitignored!)
#
# This only silences SmartScreen/"Unknown publisher" on machines that have run
# trust-FrameAssistHost.ps1 once (per-user, no admin). It is NOT a CA-issued
# code-signing certificate; for general public distribution you would need a
# real OV/EV certificate.
param([int]$Years = 10)
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot

$Name = 'FrameAssistHost'
$cert = New-SelfSignedCertificate `
    -CertStoreLocation Cert:\CurrentUser\My `
    -Subject "CN=$Name" `
    -Type CodeSigningCert `
    -Provider 'Microsoft Enhanced RSA and AES Cryptographic Provider' `
    -KeyExportPolicy Exportable `
    -KeySpec Signature `
    -KeyAlgorithm RSA -KeyLength 2048 `
    -NotBefore (Get-Date).AddDays(-1) `
    -NotAfter  (Get-Date).AddYears($Years)

# Public, DER-encoded certificate.
Export-Certificate -Cert $cert -FilePath (Join-Path $dir "$Name.cer") -Type CERT | Out-Null

# Private key, password-protected, plus the password in a gitignored file.
$password = 'FAH-' + (([Convert]::ToBase64String((1..24 | ForEach-Object { Get-Random -Maximum 256 }))) -replace '[+/=]', '').Substring(0, 24)
Export-PfxCertificate -Cert $cert -FilePath (Join-Path $dir "$Name.p12") `
    -Password (ConvertTo-SecureString -String $password -AsPlainText -Force) -Force | Out-Null
Set-Content -Path (Join-Path $dir '.signing-password.txt') -Value $password -NoNewline

Write-Host "Created $Name signing certificate:" -ForegroundColor Green
Write-Host "  CN=$Name  Thumbprint: $($cert.Thumbprint)  Valid to: $($cert.NotAfter)"
Write-Host "  .cer (public)          : $(Join-Path $dir "$Name.cer")"
Write-Host "  .p12 (PRIVATE)         : $(Join-Path $dir "$Name.p12")   <- gitignored, keep safe"
Write-Host "  password file          : $(Join-Path $dir '.signing-password.txt')  <- gitignored"
Write-Host "Next: run sign.ps1, then have recipients run trust-FrameAssistHost.ps1 once."
