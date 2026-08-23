# 🚀 MarketStore POS — Release & Auto-Updater Qo'llanmasi

Ushbu qo'llanma orqali dasturning yangi versiyasini qanday yig'ish, uni GitHub yoki shaxsiy serveringizga chiqarish va mijozlarga Telegram kabi avtomatik yetkazishni o'rganasiz.

---

## 1. 🔢 Yangi versiyani belgilash

Yangi versiya chiqarayotganingizda:
1. [`app/version.py`](../app/version.py) faylini oching:
   ```python
   APP_VERSION = "1.0.1"  # yangi versiya raqami
   ```
2. [`packaging/installer.nsi`](installer.nsi) faylidagi versiyani ham yangilang:
   ```nsis
   !define PRODUCT_VERSION "1.0.1"
   ```

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
3. Natijada `dist/` papkasida **`MarketStore_Setup_1.0.1.exe`** tayyor bo'ladi!

### 🐧 Linux uchun (AppImage)
```bash
chmod +x packaging/build_linux.sh
./packaging/build_linux.sh
```
Natija: `dist/MarketStore_1.0.1.AppImage`

### 🍏 macOS uchun (DMG)
```bash
chmod +x packaging/build_mac.sh
./packaging/build_mac.sh
```
Natija: `dist/MarketStore_1.0.1.dmg`

---

## 3. 🌐 GitHub Releases'ga chiqarish (Publishing)

1. GitHub'dagi repozitoriyangizga kiring (`https://github.com/TurdibekovBegzod/MarketStore-POS`).
2. O'ng tarafdagi **"Releases"** -> **"Draft a new release"** tugmasini bosing.
3. Ma'lumotlarni to'ldiring:
   * **Choose a tag:** `v1.0.1` (yangi tag yarating)
   * **Release title:** `MarketStore POS v1.0.1`
   * **Description:** Nimalar yangilangani (masalan: *Kassir oynasida amallar tugmasi yoqildi, tezkorlik oshirildi*).
4. **Attach binaries:** `dist/MarketStore_Setup_1.0.1.exe` (va Linux/Mac fayllarini) sudrab olib kelib yuklang.
5. **"Publish release"** tugmasini bosing!

---

## 4. 🔄 Mijozlarda yangilanish qanday ishlaydi?

1. Mijoz dasturidagi chap pastdagi profil ustiga bosadi (`Akbareliboy`).
2. **"🔄 Yangilanishlar"** tugmasini bosadi.
3. Dastur avtomatik serverga ulanadi, `v1.0.1` chiqqanini va o'zgarishlarni ko'rsatadi.
4. **"⬇️ Yuklab olish va yangilash"** tugmasi bosilgach:
   * 0% dan 100% gacha yuklanadi.
   * Yuklab bo'lingach, dastur bir marta qayta ishga tushib, eng yangi versiyaga aylanadi!
