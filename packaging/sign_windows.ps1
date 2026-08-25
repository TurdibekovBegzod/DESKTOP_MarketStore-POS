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
    $securePass = ConvertTo-SecureString -String $certPassword -Force -AsPlainText
    $selfCert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=MarketStore POS, O=MarketStore Team, C=UZ" -KeyAlgorithm RSA -KeyLength 4096 -CertStoreLocation "Cert:\CurrentUser\My" -NotAfter (Get-Date).AddYears(5)
    Export-PfxCertificate -Cert $selfCert -FilePath $certificatePath -Password $securePass | Out-Null
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
            /p $env:WINDOWS_CERTIFICATE_PASSWORD `
            /tr $timestampUrl `
            /td SHA256 `
            /d "MarketStore POS" `
            $resolvedFile

        if ($LASTEXITCODE -ne 0) {
            throw "signtool failed to sign $resolvedFile"
        }

        & $signTool.FullName verify /pa /all /v $resolvedFile
        if ($LASTEXITCODE -ne 0) {
            throw "Authenticode verification failed for $resolvedFile"
        }
    }
}
finally {
    Remove-Item -LiteralPath $certificatePath -Force -ErrorAction SilentlyContinue
}
