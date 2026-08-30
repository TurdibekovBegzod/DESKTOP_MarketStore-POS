@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "NO_PAUSE="
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"

echo ===================================================
echo   MarketStore POS - Windows Build ^& NSIS Setup
echo ===================================================
echo.

cd /d "%~dp0\.."
if errorlevel 1 goto :workspace_error

set "PYTHON_EXE=app\venv\Scripts\python.exe"
set "PYINSTALLER_EXE=app\venv\Scripts\pyinstaller.exe"

if not exist "!PYTHON_EXE!" (
    echo [XATOLIK] Python virtual environment topilmadi: !PYTHON_EXE!
    goto :failure
)

if not exist "!PYINSTALLER_EXE!" (
    echo [XATOLIK] PyInstaller topilmadi: !PYINSTALLER_EXE!
    echo O'rnatish: app\venv\Scripts\pip.exe install pyinstaller
    goto :failure
)

for /f "tokens=3" %%V in ('findstr /B /C:"APP_VERSION =" app\version.py') do set "PRODUCT_VERSION=%%~V"
if not defined PRODUCT_VERSION (
    echo [XATOLIK] app\version.py ichidan versiya olinmadi.
    goto :failure
)

echo [1/3] Versiya tekshirildi: !PRODUCT_VERSION!
echo [2/3] PyInstaller bilan production dasturi yig'ilmoqda...
"!PYINSTALLER_EXE!" --noconfirm --clean --noupx --onedir --windowed ^
    --name "MarketStore-POS" ^
    --icon "app\images\desktop_icon.ico" ^
    --version-file "packaging\version_info.txt" ^
    --add-data "app\images;images" ^
    --collect-data "certifi" ^
    --hidden-import "PyQt6" ^
    --hidden-import "sqlalchemy" ^
    --paths "app" ^
    app\main.py

if errorlevel 1 (
    echo [XATOLIK] PyInstaller dasturni yig'ishda xatolik berdi.
    goto :failure
)

if not exist "dist\MarketStore-POS\MarketStore-POS.exe" (
    echo [XATOLIK] Kutilgan dastur fayli yaratilmagan: dist\MarketStore-POS\MarketStore-POS.exe
    goto :failure
)

echo.
echo [3/3] NSIS installer kompilyatsiya qilinmoqda...
set "MAKENSIS_CMD="
where makensis.exe >nul 2>nul
if not errorlevel 1 set "MAKENSIS_CMD=makensis.exe"
if not defined MAKENSIS_CMD if exist "C:\Program Files (x86)\NSIS\makensis.exe" set "MAKENSIS_CMD=C:\Program Files (x86)\NSIS\makensis.exe"
if not defined MAKENSIS_CMD if exist "C:\Program Files\NSIS\makensis.exe" set "MAKENSIS_CMD=C:\Program Files\NSIS\makensis.exe"

if not defined MAKENSIS_CMD (
    echo [XATOLIK] makensis topilmadi. O'rnatish: winget install NSIS.NSIS
    goto :failure
)

pushd packaging
"!MAKENSIS_CMD!" "/DPRODUCT_VERSION=!PRODUCT_VERSION!" installer.nsi
set "NSIS_EXIT=!ERRORLEVEL!"
popd

if not "!NSIS_EXIT!"=="0" (
    echo [XATOLIK] NSIS installer kompilyatsiyasi muvaffaqiyatsiz tugadi.
    goto :failure
)

set "SETUP_PATH=dist\MarketStore_Setup_!PRODUCT_VERSION!.exe"
if not exist "!SETUP_PATH!" (
    echo [XATOLIK] Kutilgan setup fayli yaratilmagan: !SETUP_PATH!
    goto :failure
)

echo.
echo ===================================================
echo [MUVAFFAQIYAT] Windows production paketlari tayyor.
echo Dastur: dist\MarketStore-POS\MarketStore-POS.exe
echo Setup:  !SETUP_PATH!
echo ===================================================
call :maybe_pause
exit /b 0

:workspace_error
echo [XATOLIK] Loyiha katalogiga o'tib bo'lmadi.

:failure
call :maybe_pause
exit /b 1

:maybe_pause
if not defined NO_PAUSE pause
exit /b 0
