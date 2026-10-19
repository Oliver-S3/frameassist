# FrameAssistHost trust setup - run this ONCE on any PC that will run the signed
# FrameAssistHost.exe.
#
# Default adds the embedded FrameAssistHost code-signing certificate (a personal,
# self-signed certificate - not a Microsoft/CA-issued one) to THIS USER's
# "Trusted Root Certification Authorities" and "Trusted Publishers" stores.
#
# SmartScreen's "Unknown publisher" popup is decided against MACHINE trust, so
# if the popup still appears after the per-user install, run the -Machine variant
# from an ELEVATED (Administrator) PowerShell:
#
#     powershell -ExecutionPolicy Bypass -File .\trust-FrameAssistHost.ps1 -Machine
#
# Reversible: add -Remove to undo (same shell/scope as the install).
param([switch]$Remove, [switch]$Machine)
$ErrorActionPreference = 'Stop'

# Base64 DER of FrameAssistHost.cer (single line; base64 contains no quotes).
$cerB64 = 'MIIDBDCCAeygAwIBAgIQclajV3vxTLlEDOPiOmfCdDANBgkqhkiG9w0BAQUFADAaMRgwFgYDVQQDDA9GcmFtZUFzc2lzdEhvc3QwHhcNMjYxMDE3MjEyNjEzWhcNMzYxMDE4MjEyNjEzWjAaMRgwFgYDVQQDDA9GcmFtZUFzc2lzdEhvc3QwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQCykNfMoBBg8vBeOthzfh6O/zmgLymZjO0cmE2VdzxgQ/86nLNpay8LEKgBs0NsvaudFlzqYJSidLjx0nM3nDfUt6TjkWAiwnV9jCYH1lvCpGFsH/HCMQweXiMKg34O4BJ94jJQx0UJHQRtgJwSJBeGV82L9qs7+rKJ5XAa0APm3oD8hu90H22z3RmARp5fpkeua9AZdwidxRKZvUFtWqxXgatar9Hp5nBOqIP+rKRB409AP9ag94Kk8JrYukRIH6bE0ACvvwZo6EzcnKOgfCzZvD+CojloktrO8L2t54sOzMvDktEZYerZnL4oWWA3ZmxyXv+jYJUFCk5BLQ0oylIFAgMBAAGjRjBEMA4GA1UdDwEB/wQEAwIHgDATBgNVHSUEDDAKBggrBgEFBQcDAzAdBgNVHQ4EFgQUMzLR06MYvEhBnqe18rDadej2M5gwDQYJKoZIhvcNAQEFBQADggEBAGOeQ4oDnJQrgtYwLD5EWZuoMX9SFB/o2zgm4EINApSWl4B1uHkX19jS5OIJOEwmnZavEMJKyftqfijm4wnDNeClm+qMuRQEWfoXPEkyIiqDDkCfXM5i9HdSwQg/FYyT4K1xSFqSXbmqsmn5hcs+EGG11AXE17Gng5z35OCiwBFI7VB7Ii/oUhQbSqplVidKA0vuBZbNuZPJ+0wVHu/MSafId2GkRf8MQke9XFP7qjaGSHYwPS/T3F/feebp6JrXGA75/x3TSVTnCk7DvTqZ/58Y6RYCGM8VznTN1elqGPM9MgTlNtPfLRL22FfAFEMETl9WU9ZhgDL102ZvpcXl5og='

$script:StoreLocation = if ($Machine) { 'LocalMachine' } else { 'CurrentUser' }
$script:TrustErrors = @()
$script:RemoveErrors = @()

function Set-Stores($cert, [switch]$RemoveEntry) {
  foreach ($name in 'Root', 'TrustedPublisher') {
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store(
        $name, $script:StoreLocation)
    try {
      $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
      $have = @($store.Certificates | Where-Object Thumbprint -eq $cert.Thumbprint)
      if ($RemoveEntry) {
        foreach ($c in $have) { $store.Remove($c) }
      } elseif (-not $have) {
        $store.Add($cert)
      }
    } catch {
      if ($RemoveEntry) { $script:RemoveErrors += $name } else { $script:TrustErrors += $name }
    } finally {
      $store.Close()
    }
  }
}

$tmp = Join-Path $env:TEMP ("fah-trust-" + [guid]::NewGuid().ToString('N') + ".cer")
[IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String($cerB64.Trim()))
try {
  $cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2 $tmp
  $what = "FrameAssistHost code-signing certificate  thumbprint $($cert.Thumbprint)  valid to $($cert.GetExpirationDateString())"
  $scope = if ($Machine) { 'LocalMachine (machine-wide)' } else { 'CurrentUser (this user)' }

  Set-Stores $cert -RemoveEntry:$Remove

  if ($Remove) {
    if ($script:RemoveErrors) {
      Write-Host "Could not remove from $($script:RemoveErrors -join ', ') in $scope." -ForegroundColor Yellow
    } else {
      Write-Host "Removed FrameAssistHost trust entries ($scope). Signed builds may warn again." -ForegroundColor Yellow
    }
    return
  }

  if ($script:TrustErrors) {
    Write-Host "Warning: couldn't write to $($script:TrustErrors -join ', ') in $scope." -ForegroundColor Yellow
    if ($Machine) {
      Write-Host "Machine stores need an ELEVATED (Administrator) PowerShell - please re-run this script there."
    } else {
      Write-Host "If SmartScreen still warns, run the -Machine variant from an Administrator PowerShell."
    }
  } else {
    Write-Host "Trusted ($scope):`n  $what" -ForegroundColor Green
    Write-Host "FrameAssistHost.exe builds signed with this certificate now run without SmartScreen warnings."
  }
} finally {
  Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}
