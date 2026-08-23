#!/usr/bin/env bash
# ===================================================
#   MarketStore POS - macOS Build & DMG Script
# ===================================================
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR/.."

echo "[1/2] PyInstaller bilan macOS .app yig'ilmoqda..."
pyinstaller --noconfirm --windowed \
    --name "MarketStore POS" \
    --icon "app/images/desktop_icon.ico" \
    --add-data "app/images:images" \
    --hidden-import "PyQt6" \
    --hidden-import "openpyxl" \
    --hidden-import "barcode" \
    --paths "app" \
    app/main.py

echo "[2/2] DMG yaratilmoqda..."
if command -v create-dmg &> /dev/null; then
    create-dmg \
        --volname "MarketStore POS Installer" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --app-drop-link 420 185 \
        "dist/MarketStore_1.0.0.dmg" \
        "dist/MarketStore POS.app"
    echo "[MUVAFFAQIYAT] dist/MarketStore_1.0.0.dmg tayyor!"
else
    echo "dist/MarketStore POS.app tayyor. DMG yaratish uchun 'brew install create-dmg' o'rnating."
fi
