#!/usr/bin/env bash
# ==============================================================================
#   MarketStore POS — Universal Linux & macOS Online Installer
# ==============================================================================
set -e

API_BASE="http://169.58.152.33:8000"
GITHUB_API="https://api.github.com/repos/TurdibekovBegzod/DESKTOP_MarketStore-POS/releases/latest"

echo "======================================================"
echo "    🛒 MarketStore POS — O'rnatish Dasturi (Installer)"
echo "======================================================"
echo ""

OS_TYPE="$(uname -s)"
case "$OS_TYPE" in
    Linux*)     PLATFORM="linux";;
    Darwin*)    PLATFORM="macos";;
    *)          echo "[Xatolik] Noma'lum operatsion tizim: $OS_TYPE"; exit 1;;
esac

echo "[1/3] $PLATFORM tizimi uchun eng yangi versiya aniqlanmoqda..."

DOWNLOAD_URL=""
if curl -s -f "$API_BASE/api/v1/app/version?platform=$PLATFORM&current_version=0.0.0" > /tmp/ms_ver.json 2>/dev/null; then
    DOWNLOAD_URL=$(grep -o '"download_url":"[^"]*' /tmp/ms_ver.json | cut -d'"' -f4)
    if [[ "$DOWNLOAD_URL" == /* ]]; then
        DOWNLOAD_URL="${API_BASE}${DOWNLOAD_URL}"
    fi
fi

if [ -z "$DOWNLOAD_URL" ]; then
    echo "Serverdan topilmadi, to'g'ridan-to'g'ri GitHub Releases tekshirilmoqda..."
    if [ "$PLATFORM" = "linux" ]; then
        DOWNLOAD_URL=$(curl -s "$GITHUB_API" | grep -i "browser_download_url.*appimage" | head -n 1 | cut -d '"' -f 4)
    else
        DOWNLOAD_URL=$(curl -s "$GITHUB_API" | grep -i "browser_download_url.*dmg" | head -n 1 | cut -d '"' -f 4)
    fi
fi

if [ -z "$DOWNLOAD_URL" ]; then
    echo "[Xatolik] Yuklab olish fayli topilmadi. Iltimos internet aloqasini tekshiring."
    exit 1
fi

echo "[2/3] Yuklab olinmoqda: $DOWNLOAD_URL ..."
TMP_FILE="/tmp/marketstore_download"
curl -# -L "$DOWNLOAD_URL" -o "$TMP_FILE"

echo "[3/3] Tizimga o'rnatilmoqda..."
if [ "$PLATFORM" = "linux" ]; then
    mkdir -p "$HOME/.local/bin"
    mkdir -p "$HOME/.local/share/applications"
    DEST="$HOME/.local/bin/MarketStore-POS.AppImage"
    mv "$TMP_FILE" "$DEST"
    chmod +x "$DEST"

    cat << EOF > "$HOME/.local/share/applications/marketstore.desktop"
[Desktop Entry]
Name=MarketStore POS
Exec=$DEST
Icon=utilities-terminal
Type=Application
Categories=Office;Finance;
EOF
    echo "======================================================"
    echo "  🎉 MarketStore POS muvaffaqiyatli o'rnatildi!"
    echo "  Ishga tushirish: $DEST"
    echo "======================================================"
    nohup "$DEST" >/dev/null 2>&1 &

elif [ "$PLATFORM" = "macos" ]; then
    MOUNT_DIR=$(mktemp -d)
    hdiutil attach "$TMP_FILE" -mountpoint "$MOUNT_DIR" -nobrowse -quiet
    cp -R "$MOUNT_DIR"/*.app /Applications/ 2>/dev/null || true
    hdiutil detach "$MOUNT_DIR" -quiet
    rm -rf "$TMP_FILE"
    echo "======================================================"
    echo "  🎉 MarketStore POS muvaffaqiyatli o'rnatildi!"
    echo "  /Applications/MarketStore POS.app"
    echo "======================================================"
    open "/Applications/MarketStore POS.app"
fi
