# ==============================================================================
#   MarketStore POS — Windows PowerShell Online Installer
# ==============================================================================
$ErrorActionPreference = "Stop"

$ApiBase = "https://drinking-relight-trailside.ngrok-free.dev"
$GithubApi = "https://api.github.com/repos/TurdibekovBegzod/DESKTOP_MarketStore-POS/releases/latest"

Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "    🛒 MarketStore POS — O'rnatish Dasturi (Windows)" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] Eng so'nggi versiya aniqlanmoqda..." -ForegroundColor Yellow
$DownloadUrl = $null

try {
    $verResp = Invoke-RestMethod -Uri "$ApiBase/api/v1/app/version?platform=windows&current_version=0.0.0" -TimeoutSec 5 -Headers @{"User-Agent"="MarketStore-Installer"; "ngrok-skip-browser-warning"="true"}
    if ($verResp.download_url) {
        $DownloadUrl = $verResp.download_url
        if ($DownloadUrl.StartsWith("/")) {
            $DownloadUrl = "$ApiBase$DownloadUrl"
        }
    }
} catch {
    # Fallback to GitHub
}

if (-not $DownloadUrl) {
    try {
        $ghResp = Invoke-RestMethod -Uri $GithubApi -Headers @{"Accept"="application/vnd.github+json"; "User-Agent"="MarketStore-Installer"}
        $asset = $ghResp.assets | Where-Object { $_.name -like "*.exe" } | Select-Object -First 1
        if ($asset) {
            $DownloadUrl = $asset.browser_download_url
        } elseif ($ghResp.zipball_url) {
            $DownloadUrl = $ghResp.zipball_url
        }
    } catch {
        Write-Host "[Xatolik] Versiya ma'lumotlarini olib bo'lmadi: $_" -ForegroundColor Red
        exit 1
    }
}

if (-not $DownloadUrl) {
    Write-Host "[Xatolik] Yuklab olish havolasi topilmadi." -ForegroundColor Red
    exit 1
}

$ext = if ($DownloadUrl -like "*.zip") { ".zip" } else { ".exe" }
$TempFile = Join-Path $env:TEMP ("MarketStore_Setup_" + [int](Get-Date -UFormat %s) + $ext)

Write-Host "[2/3] Dastur yuklab olinmoqda: $DownloadUrl ..." -ForegroundColor Green
$webClient = New-Object System.Net.WebClient
$webClient.Headers.Add("User-Agent", "MarketStore-Installer")
$webClient.Headers.Add("ngrok-skip-browser-warning", "true")
$webClient.DownloadFile($DownloadUrl, $TempFile)

Write-Host "[3/3] O'rnatilmoqda..." -ForegroundColor Yellow

if ($ext -eq ".exe") {
    Start-Process -FilePath $TempFile -Wait
} else {
    $InstallDir = Join-Path $env:LOCALAPPDATA "MarketStore-POS"
    if (Test-Path $InstallDir) { Remove-Item -Path $InstallDir -Recurse -Force }
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Expand-Archive -Path $TempFile -DestinationPath $InstallDir -Force

    $ExePath = Join-Path $InstallDir "MarketStore-POS.exe"
    $WshShell = New-Object -ComObject WScript.Shell

    # Desktop shortcut
    $DesktopShortcut = $WshShell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "MarketStore POS.lnk"))
    $DesktopShortcut.TargetPath = $ExePath
    $DesktopShortcut.Save()

    # Start app
    Start-Process -FilePath $ExePath
}

Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  🎉 MarketStore POS muvaffaqiyatli o'rnatildi!" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Cyan
