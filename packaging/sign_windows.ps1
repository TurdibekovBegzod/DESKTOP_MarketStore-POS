param(
    [Parameter(Mandatory = $true)]
    [string[]] $Files
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:WINDOWS_CERTIFICATE_BASE64)) {
    throw "WINDOWS_CERTIFICATE_BASE64 GitHub secret is required."
}

if ([string]::IsNullOrWhiteSpace($env:WINDOWS_CERTIFICATE_PASSWORD)) {
    throw "WINDOWS_CERTIFICATE_PASSWORD GitHub secret is required."
}

$timestampUrl = $env:WINDOWS_TIMESTAMP_URL
if ([string]::IsNullOrWhiteSpace($timestampUrl)) {
    $timestampUrl = "http://timestamp.digicert.com"
}

$certificatePath = Join-Path $env:RUNNER_TEMP "marketstore-code-signing.pfx"
$windowsKitsRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
$signTool = Get-ChildItem -Path $windowsKitsRoot -Filter signtool.exe -Recurse |
    Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
    Sort-Object FullName -Descending |
    Select-Object -First 1

if ($null -eq $signTool) {
    throw "Windows SDK signtool.exe was not found."
}

try {
    [IO.File]::WriteAllBytes(
        $certificatePath,
        [Convert]::FromBase64String($env:WINDOWS_CERTIFICATE_BASE64)
    )

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
