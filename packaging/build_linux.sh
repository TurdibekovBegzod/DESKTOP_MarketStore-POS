#!/usr/bin/env bash
# ===================================================
#   MarketStore POS - Linux Build & AppImage Script
# ===================================================
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR/.."
PRODUCT_VERSION="$(python -c 'import sys; sys.path.insert(0, "app"); from version import APP_VERSION; print(APP_VERSION)')"

echo "[1/3] PyInstaller bilan Linux binar yig'ilmoqda..."
pyinstaller --noconfirm --onedir --windowed \
    --name "MarketStore-POS" \
    --add-data "app/images:images" \
    --hidden-import "PyQt6" \
    --hidden-import "openpyxl" \
    --hidden-import "barcode" \
    --paths "app" \
    app/main.py

echo "[2/3] AppDir tuzilmasi tayyorlanmoqda..."
APPDIR="dist/MarketStore.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$APPDIR/usr/share/applications"

cp -r dist/MarketStore-POS/* "$APPDIR/usr/bin/"
cp app/images/desktop.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/marketstore.png"
cp app/images/desktop.png "$APPDIR/marketstore.png"

cat << 'EOF' > "$APPDIR/marketstore.desktop"
[Desktop Entry]
Name=MarketStore POS
Exec=MarketStore-POS
Icon=marketstore
Type=Application
Categories=Office;Finance;
EOF

cat << 'EOF' > "$APPDIR/AppRun"
#!/bin/sh
HERE="$(dirname "$(readlink -f "${0}")")"
export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib:${LD_LIBRARY_PATH}"
exec "${HERE}/usr/bin/MarketStore-POS" "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "[3/3] AppImage yaratilmoqda..."
if command -v appimagetool &> /dev/null; then
    appimagetool "$APPDIR" "dist/MarketStore_${PRODUCT_VERSION}.AppImage"
    echo "[MUVAFFAQIYAT] dist/MarketStore_${PRODUCT_VERSION}.AppImage tayyor!"
else
    echo "[OGOHLANTIRISH] appimagetool topilmadi. AppDir tayyor: $APPDIR"
fi
