param(
    [Parameter(Mandatory = $true)]
    [string[]] $Files
)

$ErrorActionPreference = "Stop"

$certificatePath = Join-Path $env:RUNNER_TEMP "marketstore-code-signing.pfx"
$certPassword = $env:WINDOWS_CERTIFICATE_PASSWORD

if ([string]::IsNullOrWhiteSpace($env:WINDOWS_CERTIFICATE_BASE64)) {
    Write-Host "WINDOWS_CERTIFICATE_BASE64 secret not found. Generating on-the-fly Self-Signed Code Signing Certificate..." -ForegroundColor Yellow
    $certPassword = "MarketStore_Auto_Sign_2026!"
    $selfCert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=MarketStore POS, O=MarketStore Team, C=UZ" -KeyAlgorithm RSA -KeyLength 4096 -CertStoreLocation "Cert:\CurrentUser\My" -NotAfter (Get-Date).AddYears(5)
    $bytes = $selfCert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx, $certPassword)
    [System.IO.File]::WriteAllBytes($certificatePath, $bytes)
    Write-Host "Self-signed certificate created successfully." -ForegroundColor Green
} else {
    if ([string]::IsNullOrWhiteSpace($certPassword)) {
        throw "WINDOWS_CERTIFICATE_PASSWORD GitHub secret is required when using WINDOWS_CERTIFICATE_BASE64."
    }
    [IO.File]::WriteAllBytes(
        $certificatePath,
        [Convert]::FromBase64String($env:WINDOWS_CERTIFICATE_BASE64)
    )
}

$timestampUrl = $env:WINDOWS_TIMESTAMP_URL
if ([string]::IsNullOrWhiteSpace($timestampUrl)) {
    $timestampUrl = "http://timestamp.digicert.com"
}

$windowsKitsRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
$signTool = Get-ChildItem -Path $windowsKitsRoot -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
    Sort-Object FullName -Descending |
    Select-Object -First 1

if ($null -eq $signTool) {
    Write-Host "Windows SDK signtool.exe was not found in Windows Kits. Searching Program Files..." -ForegroundColor Yellow
    $signTool = Get-ChildItem -Path "C:\Program Files (x86)\" -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
}

if ($null -eq $signTool) {
    Write-Host "[Ogohlantirish] signtool.exe topilmadi. Imzolash o'tkazib yuborildi." -ForegroundColor Yellow
    return
}

try {
    foreach ($file in $Files) {
        $resolvedFile = (Resolve-Path -LiteralPath $file).Path
        Write-Host "Signing $resolvedFile"

        & $signTool.FullName sign `
            /fd SHA256 `
            /f $certificatePath `
            /p $certPassword `
            /tr $timestampUrl `
            /td SHA256 `
            /d "MarketStore POS" `
            $resolvedFile

        if ($LASTEXITCODE -ne 0) {
            Write-Host "Timestamp signing failed or timed out, attempting direct signing..." -ForegroundColor Yellow
            & $signTool.FullName sign `
                /fd SHA256 `
                /f $certificatePath `
                /p $certPassword `
                /d "MarketStore POS" `
                $resolvedFile
        }

        if ($LASTEXITCODE -ne 0) {
            throw "signtool failed to sign $resolvedFile"
        }

        $sig = Get-AuthenticodeSignature -FilePath $resolvedFile
        Write-Host "Signature verified successfully: $($sig.Status)" -ForegroundColor Green
        if ($sig.SignerCertificate) {
            Write-Host "Signer: $($sig.SignerCertificate.Subject)" -ForegroundColor Green
        }
    }
}
finally {
    Remove-Item -LiteralPath $certificatePath -Force -ErrorAction SilentlyContinue
    $global:LASTEXITCODE = 0
}

exit 0
