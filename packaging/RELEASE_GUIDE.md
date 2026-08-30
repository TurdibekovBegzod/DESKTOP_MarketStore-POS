# 🚀 MarketStore POS — Release & Auto-Updater Qo'llanmasi

Ushbu qo'llanma orqali dasturning yangi versiyasini qanday yig'ish, uni GitHub yoki shaxsiy serveringizga chiqarish va mijozlarga Telegram kabi avtomatik yetkazishni o'rganasiz.

---

## 1. 🔢 Yangi versiyani belgilash

Yangi versiya chiqarayotganda [`app/version.py`](../app/version.py) va
[`packaging/version_info.txt`](version_info.txt) ichidagi versiyani bir xil
qiling. Masalan:

1. [`app/version.py`](../app/version.py):
   ```python
   APP_VERSION = "1.3.0"
   ```
2. `packaging/version_info.txt` ichidagi `filevers`, `prodvers`,
   `FileVersion` va `ProductVersion` qiymatlarini moslang.

NSIS build script versiyani `app/version.py`dan avtomatik oladi. GitHub Actions
esa installer versiyasini release tagidan oladi.

---

## 2. 📦 Dasturni yig'ish (Build)

### 🪟 Windows uchun (NSIS Setup .exe)
1. Agar NSIS o'rnatilmagan bo'lsa, PowerShell'da bitta buyruq bilan o'rnating:
   ```powershell
   winget install NSIS.NSIS
   ```
2. [`packaging/build_windows.bat`](build_windows.bat) faylini ikki marta bosing (yoki terminalda ishga tushiring):
   ```cmd
   packaging\build_windows.bat
   ```
3. Natijada `dist/` papkasida **`MarketStore_Setup_1.3.0.exe`** tayyor bo'ladi.

### 🐧 Linux uchun (AppImage)
```bash
chmod +x packaging/build_linux.sh
./packaging/build_linux.sh
```
Natija: `dist/MarketStore_1.3.0.AppImage`

### 🍏 macOS uchun (DMG)
```bash
chmod +x packaging/build_mac.sh
./packaging/build_mac.sh
```
Natija: `dist/MarketStore_1.3.0.dmg`

---

## 3. 🌐 GitHub Releases'ga chiqarish (Publishing)

Tekshiruvlardan keyin annotatsiyalangan tagni yuboring:

```bash
git tag -a v1.3.0 -m "MarketStore POS v1.3.0"
git push origin main
git push origin v1.3.0
```

`v*` tag yuborilishi GitHub Actions orqali Windows NSIS setup, web installer,
Linux va macOS paketlarini yig'adi, imzolaydi va GitHub Release'ga avtomatik
joylaydi. Workflow muvaffaqiyatli tugamaguncha release tayyor hisoblanmaydi.

---

## 4. 🔄 Mijozlarda yangilanish qanday ishlaydi?

1. Mijoz dasturidagi chap pastdagi profil ustiga bosadi (`Akbareliboy`).
2. **"🔄 Yangilanishlar"** tugmasini bosadi.
3. Dastur avtomatik serverga ulanadi, `v1.3.0` chiqqanini va o'zgarishlarni ko'rsatadi.
4. **"⬇️ Yuklab olish va yangilash"** tugmasi bosilgach:
   * 0% dan 100% gacha yuklanadi.
   * Yuklab bo'lingach, dastur bir marta qayta ishga tushib, eng yangi versiyaga aylanadi!
