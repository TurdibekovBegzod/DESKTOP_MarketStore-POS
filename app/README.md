# Market Store POS

Python + PyQt6 asosidagi do'kon boshqaruv tizimi.

## O'rnatish

```bash
pip install PyQt6
python main.py
```

## Kirish

- Kirish online API orqali tekshiriladi: email va parol kiritiladi.
- Signup paytida emailga 3 daqiqa amal qiladigan 6 xonali tasdiqlash kodi yuboriladi.
- Har bir online account lokalda alohida `data/accounts/email-<email_sha256>/market_pos.db` bazasidan foydalanadi.
- Lokal DB fayli yo'q bo'lsa yangidan yaratiladi va shu email accountining server ma'lumotlari avtomatik olinadi.
- Default API URL: `http://169.58.152.33:8000/api/v1`
- Boshqa server ishlatilsa appni ochishdan oldin `MARKETSTORE_API_URL` env qiymatini bering.
- Parol unutilsa login oynasidagi `Parolni unutdingizmi?` orqali emailga verification code yuboriladi.

> API login muvaffaqiyatli o'tsa, user lokal bazaga email va role bilan sinxronlanadi.

## Modullar

| Modul | Funksiya |
|-------|----------|
| Sotuv | Mahsulot qidirish, barcode scanner, valyutali to'lov |
| Mahsulotlar | CRUD, kategoriya, valyutali narx, product templatelar |
| Ombor | Kirim, qoldiq nazorati |
| Mijozlar | Baza, balans, tarix |
| Hisobotlar | Umumiy jami ko'rsatkichlar, kassir va user/mijoz kesimi |
| Qarzlar | Ta'minotchilar va katta partiya qarzlarini boshqarish |
| Harajatlar | Harajat CRUD, kategoriya, valyuta va grafik hisobot |
| Kassirlar | Admin tarafidan kassir/admin foydalanuvchilarini boshqarish |
| Kirish tarixi | Kim qachon tizimga kirganini ko'rish |

## Yaxshilangan joylar

- Sotuvlar tranzaksiya bilan saqlanadi: qoldiq yetmasa sotuv bekor qilinadi.
- Qarz savdoda mijoz tanlash majburiy va summa mijoz balansiga yoziladi.
- Ombor kirimlari va sotuv chiqimlari `stock_movements` tarixida saqlanadi.
- Mahsulot shtrix-kodi takrorlansa yoki narx kiritilmasa, aniq ogohlantirish chiqadi.
- Sotuv tarixida bor mahsulotni o'chirish bloklanadi.
- Product template yaratish mumkin: template hususiyatlari mahsulot qo'shishda avtomatik maydon bo'lib chiqadi.
- Barcode scanner Enter yuborganda mahsulot shtrix-kod orqali topilib savatga qo'shiladi.
- Valyuta kurslari kiritiladi: UZS, USD, EUR yoki boshqa valyutada to'lov qabul qilinadi.
- Mahsulot sotish va xarid narxini USD, EUR, UZS yoki boshqa valyutada kiritish mumkin; tizim so'mga aylantirib saqlaydi.
- Admin kassir qo'shadi; kassir login qilganda faqat sotuv oynasini ko'radi.
- Har bir muvaffaqiyatli login tarixi saqlanadi: username, role va kirgan vaqti.
- Hisobotlarda umumiy daromad, foyda, chek soni va o'rtacha chek grafiklari bor.
- Kassir yoki mijoz tanlanganda uning haftalik/oylik daromad, foyda, chek soni va o'rtacha chek grafigi chiqadi.
- Ta'minotchilar bo'yicha qarz qo'shish, to'lov qilish va qarz tarixini ko'rish mumkin.
- Mahsulot qo'shishda mahsulot kimdan olinganini tanlash mumkin.
- Harajatlar kategoriya, valyuta va description bilan yuritiladi; kunlik/haftalik/oylik grafik hisobot bor.

## Fayl tuzilmasi

```text
market_pos/
|-- main.py          # Kirish nuqtasi
|-- database.py      # SQLite + barcha so'rovlar
|-- requirements.txt
|-- data/
|   `-- market_pos.db # Lokal baza fayli
|-- docs/
|   `-- build.txt
|-- tools/
|   `-- make_project_doc.py
|-- ui/
|   |-- login_dialog.py
|   |-- main_window.py
|   |-- sales_widget.py
|   |-- products_widget.py
|   |-- stock_widget.py
|   |-- customers_widget.py
|   `-- reports_widget.py
`-- utils/
    `-- (kelajakda: pdf chek, import/export)
```
