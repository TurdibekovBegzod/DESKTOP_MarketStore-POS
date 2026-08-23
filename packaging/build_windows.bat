@echo off
echo ===================================================
echo   MarketStore POS - Windows Build & NSIS Setup
echo ===================================================
echo.

cd /d "%~dp0\.."

echo [1/3] Python Virtual Environment faollashtirilmoqda...
call app\venv\Scripts\activate.bat

echo [2/3] PyInstaller bilan dastur yig'ilmoqda (onedir)...
pyinstaller --noconfirm --onedir --windowed ^
    --name "MarketStore-POS" ^
    --icon "app\images\desktop_icon.ico" ^
    --add-data "app\images;images" ^
    --hidden-import "PyQt6" ^
    --hidden-import "openpyxl" ^
    --hidden-import "barcode" ^
    --hidden-import "psycopg" ^
    --hidden-import "sqlalchemy" ^
    --paths "app" ^
    app\main.py

if %ERRORLEVEL% NEQ 0 (
    echo [XATOLIK] PyInstaller dasturni yig'ishda xatolik berdi!
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo [3/3] NSIS Installer kompilyatsiya qilinmoqda...
set MAKENSIS_CMD=""
where makensis >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set MAKENSIS_CMD=makensis
) else if exist "C:\Program Files (x86)\NSIS\makensis.exe" (
    set MAKENSIS_CMD="C:\Program Files (x86)\NSIS\makensis.exe"
) else if exist "C:\Program Files\NSIS\makensis.exe" (
    set MAKENSIS_CMD="C:\Program Files\NSIS\makensis.exe"
)

if not %MAKENSIS_CMD%=="" (
    %MAKENSIS_CMD% packaging\installer.nsi
    echo.
    echo ===================================================
    echo [MUVAFFAQIYAT] Setup fayli muvaffaqiyatli tayyorlandi!
    echo Joylashuvi: dist\MarketStore_Setup_1.0.0.exe
    echo ===================================================
) else (
    echo [OGOHLANTIRISH] makensis topilmadi.
    echo NSIS o'rnatish uchun: winget install NSIS.NSIS
    echo Dastur papkasi tayyor: dist\MarketStore-POS\
)

pause
