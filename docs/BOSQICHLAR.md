# MarketStore POS — bosqichlar bo'yicha ish holati

> Bu fayl **qayerda to'xtaganimizni** yozib qo'yish uchun. Yangi sessiya ochilsa
> yoki oradan vaqt o'tsa, shu fayldan boshlang: nima qilingan, nega shunday
> qilingan, keyingi qadam nimadan boshlanadi.
>
> Oxirgi yangilanish: **2026-08-27** · Chiqarilgan versiya: **v1.1.5**
> · 2-bosqich va tuzatishlar hali commit qilinmagan

---

## Holat

**Jami 7 ta bosqich: 0 dan 6 gacha. To'rttasi tugadi, uchtasi qoldi.**

| Bosqich | Mazmuni | Holat | Versiya | Taxminiy vaqt |
|---|---|---|---|---|
| **0** | Mavjud hisob-kitob xatolarini tuzatish | ✅ Tugadi, chiqarildi | `v1.1.3` | — |
| **1** | Yagona identifikator (UUID) | ✅ Tugadi, chiqarildi | `v1.1.5` | — |
| **2** | Pul raqamlarining asosi (o'zgarmas jurnal) | ✅ Tugadi | commit qilinmagan | — |
| **3** | Real-time (Telegram kabi) | ✅ Tugadi | commit qilinmagan | — |
| **4** | Eskirgan ma'lumotdan himoya | ⬜ Boshlanmagan | — | ~1 hafta |
| **5** | Qurilmalararo bildirishnoma | ⬜ Boshlanmagan | — | ~3–4 kun |
| **6** | Sync tugmasini olib tashlash | ⬜ Boshlanmagan | — | ~1 hafta |

Tartib muhim: **avval raqamlarni to'g'ri qilamiz (2), keyin ularni tez
tarqatamiz (3)**. Teskarisi qilinsa, noto'g'ri raqam bir zumda hamma
qurilmaga yetib boradi va qaysi biri to'g'riligini aniqlash imkonsiz bo'ladi.
6-bosqich esa faqat 4 tugagandan keyin bo'lishi mumkin — hozir sync tugmasi
konfliktni hal qiladigan yagona yo'l.

Asosiy reja dastlab suhbatda tuzilgan edi; **shu fayl endi uning to'liq
o'rnini bosadi** — bajarilgani, chetga chiqishlar va qolgan bosqichlar
hammasi shu yerda.

Oraliqda `v1.1.4` ham chiqdi — u bosqichlarga kirmaydi: server tomondagi
**superadmin panel va "remote purge"** (web paneldan account ma'lumotini
o'chirish, qurilmalar uni bir marta qo'llashi).

---

## 0-bosqich — Mavjud hisob-kitob xatolarini tuzatish ✅

Sinxronizatsiyaga tegilmadi, faqat lokal to'g'rilik:

- `_apply_stock_delta()` — qoldiq va harakat jurnali **doim birga** yoziladi
- `_cleanup_unfinalized_sales_for_product` endi qoldiqni qaytaradi va
  kompensatsiya harakati yozadi (ilgari tovar oqib ketardi)
- `add_product` boshlang'ich qoldiq harakatini yozadi
- `update_product` qoldiqni izsiz o'zgartira olmaydi
- `finish_inventory_check(apply_corrections=True)` — inventarizatsiyadan keyin
  tuzatish yoziladi
- `_SYNC_SUSPENDED` global o'zgaruvchi → `threading.local()` (kassir sotuv
  qilayotganda yozuvi navbatdan tushib qolmasligi uchun)
- `sync_outbox` yozuvi ma'lumot bilan **bitta tranzaksiyada**
  (`_flush_session_outbox`), va `seq INTEGER PRIMARY KEY AUTOINCREMENT` oldi
- `mark_sync_pushed(up_to_seq=...)` faqat yuborilganini o'chiradi
- Bulk `query.delete()/update()` ishlatadigan joylar ORM sikliga o'tkazildi

Isbot: eski kodda `stock=60 jurnal=10` (50 dona yo'qolgan), yangi kodda
`stock=90 jurnal=90`.

---

## 1-bosqich — Yagona identifikator (UUID) ✅

### Qabul qilingan qarorlar

Bular **siz tanlagan** qarorlar, keyin o'zgartirmoqchi bo'lsangiz sababini
bilib turing:

1. **Hamma yozuv UUID bilan ishlaydi** — oddiy `id` emas. Rejada `uid TEXT`
   qo'shimcha ustun deb yozilgandi; siz "hamma tomon uuid bilan ishlasin"
   deganingiz uchun **kalitning o'zi** UUID qilindi.
2. **Eski ma'lumot saqlanadi va ko'chiriladi.** Bu qaror ikki marta
   o'zgardi: avval determinstik UUID5 bilan ko'chirish yozildi, keyin siz
   "tozalab tashlagin" deganingizdan keyin tozalashga o'tkazildi, so'ng yana
   ko'chirishga qaytarildi. **Hozirgi holat — ko'chirish:** mahsulotlar,
   sotuvlar, kassirlar, sozlamalar hammasi joyida qoladi, faqat
   identifikatorlar UUID ga aylanadi. Har qurilma bir xil formuladan
   hisoblagani uchun ular mos tushadi.
3. **"Sotuv #12" uchun alohida ko'rsatish raqami** (`sales.display_no`).
   U identifikator emas — ikki qurilmada takrorlansa ham hech narsa buzilmaydi.
4. **`currency` o'z joyida qoladi.** Rejada uni `user_settings` ga ko'chirish
   bor edi (bitta kassir USD ga o'tsa hammada o'zgarmasin deb); siz "bu shart
   emas" dedingiz. Kod qaytarib qo'yildi. *Ya'ni hozir ham currency
   sinxronlanadi va hamma qurilmada bir xil bo'ladi.*
5. **Qurilma sloti kerak emas.** Rejadagi `POST /sync/device/slot` raqam
   taqsimlash uchun edi; UUID da taqsimlash degan narsa yo'q.

### Baza — `app/database.py`

**Identifikator yordamchilari** (`ROW_ID_NAMESPACE` yonida):

- `new_row_id()` — yangi qator uchun `uuid4`
- `stable_row_id(table, key)` — `uuid5(NAMESPACE, "jadval:kalit")`.
  Har qurilma bir xil natija beradi. Standart qatorlar shundan foydalanadi.
- `owner_row_id(account_uid)` — **account egasining `users.id` si**.
  Account uid dan hisoblanadi, ya'ni hamma qurilmada bir xil.
- `is_row_uuid(value)` — kelgan yozuv haqiqiy UUID mi

> ⚠️ `ROW_ID_NAMESPACE` ni **hech qachon o'zgartirmang** — o'zgartirilsa hamma
> o'rnatmadagi standart qatorlar identifikatorlari qaytadan aralashadi.

**Sxema:** 23 ta jadvalning PK si va 26 ta ForeignKey `Integer` → `String(36)`.
`app_settings` (kaliti nom) va `account_assets` (kaliti asset nomi) tegilmadi.
`sync_outbox.seq` **INTEGER AUTOINCREMENT bo'lib qoladi** — u identifikator
emas, yuborish suv belgisi.

**`sales.display_no`** — `_next_sale_display_no(session)` `max+1` beradi.
`_sale_label(session, sale_id)` — odam o'qiydigan nom (raqam bo'lmasa UUID ning
birinchi 8 belgisi).

**Migratsiya `012_uuid_row_identity`:**

1. `users.id` hali INTEGER bo'lsa ishlaydi, aks holda darrov chiqadi
2. Account egasi topiladi (`user_settings.api_user_uid` orqali), uning qatori va
   sozlamalari (API tokeni ham) yodda saqlanadi
3. `app_settings` dan boshqa hamma jadval **DROP** qilinadi va
   `Base.metadata.create_all` bilan UUID sxemasida qayta quriladi
4. Egasi `owner_row_id(account_uid)` bilan qayta yoziladi, sozlamalari tiklanadi
5. `sync_outbox` tozalanadi
6. `identity_reset_required=1` va `server_reseed_required=1` qo'yiladi

**`run_migrations()` o'zgardi:** endi tranzaksiya ochilishidan **oldin**
`PRAGMA foreign_keys=OFF` beradi va oxirida `engine.dispose()` qiladi.
Sababi: SQLite bu pragma ni tranzaksiya ichida **e'tiborsiz qoldiradi**, jadval
qayta qurilayotganda esa unga ishora qiluvchi hamma cheklov buziladi.
*(Bu sinovda haqiqiy xatoga olib kelgan edi — `eliboyakbar` bazasida mavjud
bo'lmagan kassirga ishora qiluvchi 3 ta sotuv bor.)*

**Import himoyasi (`import_sync_records`):**

- Yangilanmagan qurilmadan kelgan **integer identifikatorli** yozuv qabul
  qilinmaydi (`skipped_legacy`, `sync_state.last_pull_skipped_legacy`)
- Nom bo'yicha to'qnashuv (`UNIQUE`) endi **butun yuklashni to'xtatmaydi**:
  `_clear_conflicting_unique_rows()` eski qatorni bo'shatadi, so'ng qayta
  urinadi; baribir bo'lmasa faqat o'sha bitta yozuv tashlanadi
  (`sync_state.last_pull_rejected`)

**Standart qatorlar determinstik identifikator oladi** — valyutalar,
kategoriyalar, "Umumiy" bo'lim, "Umumiy mahsulot" shabloni, harajat turlari
("Kassir" ham). Aks holda ikki qurilma bir xil nomni ikki marta yaratib,
`UNIQUE` da to'qnashardi.

**Egasining identifikatorini tuzatish:** `_rekey_user_identity(session, user,
new_id)` — mavjud egani `owner_row_id` ga ko'chiradi (vaqtinchalik nom bilan,
chunki `username`/`email` unique), hamma ishoralarni va sozlamalarni olib
o'tadi. `init_db` da chaqiriladi.

**Tartib qoidalari:**

- `_first_admin_id` → **`_account_owner_id`**. Ilgari "eng kichik `id` li
  admin" edi — bu kimga tegishlisiz harajatlarni kim ko'rishini hal qiladigan
  biznes qoida. UUID da ma'nosini yo'qotadi, shuning uchun endi **account
  egasi**, u topilmasa eng erta yaratilgan admin.
- `ORDER BY ProductSection.id` / `ProductTemplate.id` → `created_at, name`
- `ORDER BY users.id DESC` (`:285`) **tegilmadi** — u eski (integer) bazadan
  ko'chirish so'rovi

### Sinxronizatsiya — `app/sync_service.py`

- `push_local_changes`: `identity_reset_required` bo'lsa avval
  **`force_upload`** ga o'tadi — server tozalanadi va to'liq qayta yuklanadi.
  Bo'lmasa keyingi "Olish" o'chirilgan eski yozuvlarni qaytarib olib kelardi.
- `force_upload`: `reset_sync_records` muvaffaqiyatli bo'lgach
  `mark_identity_reset_complete()`
- `pull_server_changes`: bayroq turgan bo'lsa **rad etadi** va foydalanuvchiga
  "avval Yuborish" deydi

### Interfeys

- `reports_widget.py` — `int(sale_item_id)` ikki joyda `str(...)` ga o'zgardi
  (UUID da `int()` xato beradi). Ikkalasi ham faqat bir xil vaqtli qatorlarni
  ajratish uchun.
- `products_widget.py`, `sales_widget.py` — `#{sale_id}` o'rniga
  `sale_display_no` / `db.get_sale_display_no()`
- `products_widget.py` shablon "ID" ustuni va `login_history_widget.py`
  "User ID" ustuni — UUID ning birinchi 8 belgisi
- Bazadagi qatorlarga `sale_display_no` qo'shildi
  (`get_product_sales_archive`, `get_cashier_sales_details`)

### Testlar

**Jami 135 ta test o'tadi** (1-bosqichdan oldin 122 ta edi).

Yangi:

- `app/tests/test_row_identity.py` (9 ta) — UUID berilishi, egasining barqaror
  identifikatori, standart qatorlar mosligi, `display_no` sanashi, migratsiya
  tozalashi va egani saqlab qolishi, integer yozuvni rad etish, nom
  to'qnashuvini hal qilish
- `app/tests/test_two_device_identity.py` (4 ta) — **rejadagi og'ir
  tekshiruvlar**:
  - ikki bazada 300 tadan sotuv → aylanishdan keyin 600 ta alohida yozuv,
    bitta ham umumiy identifikator yo'q
  - o'chirilgan sotuv tirilmaydi (keyingi 50 ta sotuv uni qaytarmaydi)
  - kassir oyligi ikki qurilmada **bir xil raqam**
  - account egasi ikkala qurilmada bitta odam

Yangilangan: `test_database_orm_regression.py` (eski baza endi tozalanadi, ko'chirilmaydi),
`test_realtime_sync.py` (UUID li server yozuvlari), `test_account_database_isolation.py`,
`test_stock_ledger.py` (jurnal `rowid` bo'yicha o'qiladi, `id` bo'yicha emas).

### Haqiqiy bazalarda tekshiruv

To'rtta account bazasining **nusxasi** (asl fayllarga tegilmagan) to'liq yo'ldan
o'tkazildi: migratsiya → `init_db` → mahsulot qo'shish → kassir qo'shish →
sotuv → moliya hisoboti → sotuv tafsiloti → arxiv → eksport. Hammasi toza,
egasining identifikatori to'g'ri.

Tekshiruv paytida topilgan haqiqiy nuqson: `eliboyakbar@gmail.com` bazasida
**3 ta sotuv mavjud bo'lmagan kassirga** (`cashier_id=2`) ishora qilyapti —
rejadagi "B" muammosining jonli isboti. Tozalash bilan u ham ketdi.

---

## 2-bosqich — Pul raqamlarining asosi ✅

Har bir pul raqami endi **hech qachon qayta yozilmaydigan qatorlardan**
hisoblanadi. Ustunlar faqat kesh bo'lib qoldi.

### Nima buzuq edi

Qaytarish sotuvning `total`, `discount`, `paid` ustunlarini **o'chirib qayta
yozardi**. Sotuv o'zi qanday bo'lganini unutardi, shuning uchun bir xil
qaytarish ikki marta qo'llansa, buni ikkita haqiqiy qaytarishdan ajratib
bo'lmasdi. Foyda esa mahsulotning **hozirgi** tannarxini o'qirdi — ya'ni
tannarxni bugun o'zgartirsangiz, o'tgan oylarning foydasi qayta yozilardi.

### Nima qilindi

**Muhrlangan asl summalar** — `sales.original_total`, `original_discount`,
`original_paid`, `original_cashier_reward`. Yozilgandan keyin ularga hech kim
tegmaydi; ko'rinadigan summalar har safar shulardan va qaytarishlardan qayta
hisoblanadi (`_recalculate_sale`). Ayirish yo'q, shuning uchun takroriy
qaytarish natijani surib yubormaydi.

**`sale_returns` jadvali** — qaytarish endi hisoblagich emas, **qator**. O'z
UUID si bor, ya'ni sinxronizatsiyadan yoki ikki marta bosishdan kelgan nusxa
o'sha qatorning ustiga tushadi va hech narsani o'zgartirmaydi.
`returned_quantity` shu qatorlardan hisoblanadi.

**`sale_items.cost_at_sale`** — tannarx sotuv paytida muhrlanadi. Bitta
`_item_cost()` funksiyasi hamma joyda ishlatiladi: sotuv arxivi, mahsulot
foydasi, bo'lim hisoboti, sotuv tafsiloti. Qo'shimcha `created_at` /
`updated_at` ham qo'shildi — ilgari sotuv qatorida vaqt umuman yo'q edi.

**`customer_debt_movements` jadvali** — mijoz qarzi endi jurnal.
`customers.balance` shu jurnalning yig'indisi (`_recalculate_customer_balance`).

**Qarzdor va ta'minotchi balanslari** ham jurnaldan hisoblanadigan bo'ldi
(`_recalculate_debt_balance`). Ilgari `balance` ustuni harakat qatorlari yonida
alohida qo'shilib-ayirilardi va hech kim ikkalasini solishtirmasdi — shuning
uchun qarzdorlar oynasi bilan moliya hisoboti har xil raqam ko'rsatishi mumkin
edi. `total_given` / `total_received` ham shu yerdan chiqadi.

**`cashier_reward`** yakunlashda bir marta muhrlanadi; qaytarish uni sotuvga
nisbatan ulush bo'yicha kamaytiradi, lekin **har safar muhrlangan qiymatdan**
hisoblanadi.

**Sotuv qatori o'chirilsa** — unga qilingan qaytarishlar saqlanib qoladi
(`sale_item_id` NULL bo'ladi), shunda sotuv summasi tushunarli bo'lib qoladi.
Sotuvning o'zi o'chirilsa, uning qaytarishlari ham ketadi, lekin mijozning
qarzi qolaveradi — u haqiqatan sodir bo'lgan.

### Migratsiya `013_money_ledger`

Yangi ustunlar va ikkita jadval qo'shiladi, mavjud qatorlar hozirgi qiymati
bilan muhrlanadi, mijozning mavjud qarzi jurnalga "boshlang'ich qoldiq" bo'lib
tushadi.

⚠️ **`ledger_baseline_at`.** 1-bosqich ma'lumotni saqlab qolgani uchun,
migratsiyadan **oldin** qaytarish qilingan sotuvlarning asl summasi
tiklanmaydi — eski kod uni o'chirib yuborgan. Shuning uchun migratsiya
"jurnal shu paytdan boshlandi" degan belgi qo'yadi
(`db.get_ledger_baseline()`), va undan oldingi raqamlar **meros** deb
qaraladi. Sizning bazangizda: `eliboyakbar` — 4 ta, `begzodasidev` — 17 ta
meros sotuv.

### Yangi funksiyalar

`recalculate_sale_totals(sale_id)`, `get_sale_returns(sale_id)`,
`get_customer_debt_movements(customer_id)`, `get_ledger_baseline()`.

### Testlar

`app/tests/test_money_ledger.py` — 10 ta:

- qaytarish muhrlangan summalarga tegmasligi
- hammasini qaytarish chegirmani va mukofotni **to'liq** qaytarishi
- takroriy qaytarish qatori hech narsani o'zgartirmasligi
- `returned_quantity` qaytarish qatorlari yig'indisiga tengligi
- sotilganidan ko'p qaytarishning rad etilishi
- tannarxni o'zgartirish o'tgan foydani qayta yozmasligi
- mijoz balansi o'z jurnaliga tengligi
- qatorni o'chirish qaytarishlarni saqlab qolishi
- qarzdor va ta'minotchi balanslari jurnalga tengligi

Jami **151 ta test** o'tyapti (2-bosqichdan oldin 122 ta edi).

`finance_widget.py` va `finance_excel.py` tekshirildi: ular o'zlari hech qanday
tannarx/foyda hisoblamaydi, `database.py` bergan raqamlarni ko'rsatadi —
shuning uchun tuzatish ularga o'z-o'zidan yetib bordi.

---

## 3-bosqich — Real-time ✅

Tugma bosish shart emas: boshqa qurilmadagi o'zgarish o'zi kelib qo'llanadi,
ochiq oyna o'zi yangilanadi.

### Nima qilindi

**O'qilgan joy eslab qolinadi** — `sync_state.pull_watermark`. Server
`?since=` orqali faqat o'zgarganini berish imkoniyatini **doim** qo'llab
kelgan, mijoz esa undan hech qachon foydalanmagan: har yuklash butun
accountning nusxasi edi. Endi belgi saqlanadi va `since` sifatida yuboriladi.
Belgi **faqat hech narsa tashlanmagan bo'lsa** oldinga suriladi — aks holda
o'tkazib yuborilgan qator belgidan orqada qolib, boshqa hech qachon
taklif qilinmagan bo'lardi.

**Yuklash bo'laklarga bo'lindi** — `IMPORT_CHUNK_SIZE = 200`. Ilgari bir necha
ming qatorli yuklash bitta tranzaksiya edi va shu vaqt davomida dastur javob
bermay turardi. Endi har bo'lak alohida, va xato bo'lsa butun yuklash emas,
bitta bo'lak yo'qoladi.

**Kelgan ma'lumot ustidan hisob-kitob tiklanadi** — yuklash `sale_returns`
qatorini uni tilga oluvchi `sales` qatorisiz keltirishi mumkin, shuning uchun
tegilgan sotuvlarning keshlangan raqamlari qayta hisoblanadi
(`_reconcile_imported_sales`). Bu suspend ichida bo'ladi: hosila qiymat qayta
serverga ketmaydi.

**Bitta aylanish** — `sync_service.auto_sync_turn()`: **avval oladi, keyin
beradi**. Ataylab shunday: o'z ishini boshqa tomonnikini ko'rmasdan yuborgan
qurilma oddiy tahrirni konfliktga aylantiradi. Konflikt chiqsa bir marta
to'liq o'qib qayta urinadi, keyin tugmaga qoldiradi.

**Ikkita sinxronizatsiya bir vaqtda ishlamaydi** — `_SYNC_LOCK`. Avtomatik
dvigatel ham, tugma ham shu funksiyalarga kiradi; ustma-ust tushgan aylanishlar
bir xil qatorlarni ikki marta yuborardi.

**`app/sync_engine.py` — `SyncEngine`.** O'z oqimida ishlaydigan bitta ishchi.
Uch sababdan biri bo'lsa aylanish qiladi:

| Sabab | Kutish |
|---|---|
| Server "o'zgarish bor" dedi (SSE `change`) | darrov |
| Bu qurilma o'zi nimadir yozdi | 700 ms — bir sotuvning bir nechta yozuvi bitta yuborishga birlashadi |
| Hech kim hech narsa demadi | 30 s — faqat oqim uzilgan holat uchun himoya |

Xato bo'lsa 15 soniya kutadi va **hech qanday xabar chiqarmaydi** — kassirning
ekraniga proksi uzilishi haqida yozish keraksiz, holat ko'rsatkichi allaqachon
aytib turibdi.

**Ochiq oyna o'zi yangilanadi.** `change` hodisasi endi "yuklab oling" degan
xabar emas, dvigatelga buyruq. Yuklashdan keyin `_reload_current_page()`
ishlaydi. Kerakli ekranlarning **hammasi** `QStackedWidget` ichidagi sahifa —
sotuv tafsiloti, mahsulotlar, hisobotlar, moliya, qarzlar, harajatlar — ya'ni
ko'rinib turgani darhol, qolganlari ochilganda yangilanadi (`_switch_page`
har safar `load_data()` chaqiradi).

**Internetsiz pul yozib bo'lmaydi.** `db.require_online()` to'qqizta joyda:
sotuvni yakunlash, tasdiqlash, qaytarish, sotuv yozuvini o'chirish, harajat,
qarz berish/to'lash, ta'minotchi qarzi/to'lovi. Ko'rish, qidirish, hisobot —
hammasi ishlayveradi. Aloqa holati noma'lum bo'lsa **onlayn** deb hisoblanadi,
ya'ni tekshiruv o'zi to'sqinlik qilmaydi.

**Sync tugmasi joyida qoladi** — zaxira yo'l sifatida. U faqat 6-bosqichda
olib tashlanadi.

### Testlar

`app/tests/test_auto_sync.py` (8 ta) va `app/tests/test_sync_engine.py` (7 ta):

- birinchi yuklash to'liq, keyingisi faqat o'zgarishni so'rashi
- tashlangan qator bo'lsa belgi oldinga surilmasligi
- aylanish avval olib keyin berishi
- yuboradigan narsa bo'lmasa yubormasligi
- konflikt bir marta qayta o'qib urinishi
- 450 qator 100 talik bo'laklarda qo'llanishi
- dvigatel bekorga ishlamasligi, ishlashi kerak bo'lganda ishlashi
- uzilishda jim qolib kutishi, konfliktni tugmaga topshirishi
- internetsiz pul yozuvlari rad etilishi, ko'rish ishlayverishi

Jami **169 ta test** o'tyapti.

### Nima qilinmadi

Bu bosqichda ham sinxronizatsiya hali **butun qator** darajasida ishlaydi:
ikki qurilma bitta qatorni bir vaqtda o'zgartirsa, oxirgisi yutadi. Undan
himoya — **4-bosqich**.

---

## 4-bosqich — Eskirgan ma'lumotdan himoya ⬜

Sizning talabingiz: *"B qurilma hali xabar olmagan bo'lsa nima bo'ladi"*.

### Muammo

Ikki kassir bir vaqtda ishlayapti. A da mahsulot sotildi, qoldiq 3 ga tushdi.
B hali buni olmagan — unda 5 ko'rinib turibdi. B kassir mahsulotni tahrirlaydi
va **o'zi ko'rgan holatni** yuboradi → A ning sotuvi qoldiqdan yo'qoladi.
Hozir bunga hech qanday to'siq yo'q.

### Nimasi allaqachon tayyor

Server har yozuv uchun `user_records.sync_version` saqlaydi: qo'shilganda `1`,
har o'zgarishda `+1`. U `RecordOut` sxemasida bor, ya'ni **har `/sync/pull`
javobida qaytadi**. Lekin **mijoz uni umuman o'qimaydi** — `api_client.py`,
`sync_service.py`, `database.py` da bu so'z bir marta ham uchramaydi.

### Qilinadigan ish

1. Mijoz har yozuv uchun "men ko'rgan versiya" ni saqlaydi
2. O'zgartirish yuborganda uni ham jo'natadi — `expected_version`
3. Server solishtiradi; o'zinikida yangiroq versiya bo'lsa **o'sha bitta
   qatorni** rad etadi
4. **409 emas, qisman muvaffaqiyat** — qolgan yozuvlar o'tadi, rad etilgani
   javobning `rejected` ro'yxatida qaytadi
5. Dastur *"Bu mahsulot boshqa qurilmada o'zgardi (sotildi)"* deb ko'rsatadi va
   o'sha qatorning yangi holatini yuklab oladi

### Eng muhim qoida

**Sotuv va qaytarish hech qachon rad etilmaydi.** Ular yangi qator qo'shish —
to'qnashadigan narsa yo'q, ayniqsa identifikator UUID bo'lgandan keyin. Faqat
**tahrirlash** rad etilishi mumkin: narx, qoldiq, kassir ma'lumoti, sozlama.
Ya'ni kassir sotuv qilayotganda hech qachon "rad etildi" xabarini ko'rmaydi.

### Fayllar

| Fayl | Nima |
|---|---|
| `api/app/routers/sync.py` | `sync_version` bo'yicha shartli upsert |
| `api/app/schemas.py` | `expected_version`, javobda `rejected` |
| `app/database.py` | ko'rilgan versiyani saqlash |
| `app/sync_service.py` | versiyani yuborish, rad etilganini qaytarish |
| `app/ui/main_window.py` | rad etish xabari + o'sha qatorni qayta yuklash |

**Bog'liqlik:** 3-bosqichdan keyin. Real-time bo'lmasa rad etish tez-tez sodir
bo'ladi va bezovta qiladi; real-time bilan qurilmalar deyarli har doim yangi
holatda bo'ladi.

---

## 5-bosqich — Qurilmalararo bildirishnoma ⬜

*"Sardor: Lenovo Ideapad sotdi"* — hamma qurilmada.

### Hozirgi holat

`activity_logs` jadvali **mavjud** (model bor, `005_create_activity_logs`
migratsiyasi bor, haqiqiy bazalarda jadval yaratilgan) — lekin **unga hech
qachon hech narsa yozilmaydi**. Haqiqiy bazalarda qator soni: **0**.

`log_activity()` faqat jarayon xotirasidagi `_SESSION_ACTIVITIES` ro'yxatiga
yozadi, identifikator sifatida `_ACTIVITY_COUNTER` degan hisoblagichdan
foydalanadi — u dastur har ochilganda **noldan boshlanadi**. O'qilgan
bildirishnomalar ham xotirada (`_SESSION_READ_IDS`), `notification_reads`
jadvaliga yozilmaydi.

Shuning uchun **hozir qurilmalararo bildirishnoma texnik jihatdan imkonsiz**.

### Qilinadigan ish

1. `log_activity` jadvalga ham yozsin. **41 ta chaqiruv joyiga tegilmaydi** —
   faqat funksiyaning o'zi o'zgaradi.
2. `activity_logs` `SYNC_TABLES` ga qo'shilsin. ⚠️ Tuple **ota-ona birinchi**
   tartibida va bu tartib import/wipe uchun ishlatiladi — jadvalni oxiriga
   qo'shib qo'yish emas, **to'g'ri chuqurlikka** joylash kerak.
3. `activity_logs` ga **kim qilgani** ustuni qo'shilsin (`user_id`) — hozir
   unday ustun yo'q, "Sardor sotdi" deyish uchun kerak.
4. `device_key` solishtirilsin — o'z amalini o'ziga ko'rsatmaslik uchun.
5. `notification_reads.notification_id` endi haqiqiy UUID ni saqlasin (hozir
   `act_<hisoblagich>` kabi sun'iy qiymat, dastur qayta ochilganda ma'nosini
   yo'qotadi).

---

## 6-bosqich — Sync tugmasini olib tashlash ⬜

Sizning beshinchi talabingiz.

### Nega eng oxirida

Hozir "Yuborish/Olish" tugmasi shunchaki qulaylik emas — u **409 konfliktni hal
qiladigan yagona yo'l**. Server "sizning nusxangiz eskirgan" desa,
`ConflictDialog` ochiladi va siz qaysi tomon yutishini tanlaysiz. Uni olib
tashlashdan oldin o'rniga 4-bosqich (qator darajasidagi rad etish) kelishi
kerak.

Tartib: **0 → 1 → 3 → 4 → 6**.

### Qilinadigan ish

1. **`SyncDialog` va `ConflictDialog` o'chiriladi.**
   ⚠️ Bitta ehtiyot: hozir `SyncDialog` ichida admin uchun *"Serverni shu
   qurilmadagiga almashtirish"* tugmasi ham bor — unga yangi joy kerak
   (Sozlamalar ichiga).
2. **Import qatorma-qator xatoga chidamli bo'ladi** — `sync_quarantine`
   jadvali. Bu eng muhim qism: tugma bo'lmasa, sinxronizatsiya **jimgina**
   to'xtab qolishi va siz bilmay qolishingiz mumkin.

   Bu ishning bir qismi **1-bosqichda allaqachon qilindi**: nom bo'yicha
   to'qnashuv butun yuklashni to'xtatmaydi (`_clear_conflicting_unique_rows`),
   rad etilgan va tashlangan yozuvlar sanalib `sync_state` ga yoziladi
   (`last_pull_rejected`, `last_pull_skipped_legacy`). Hali **karantin jadvali
   yo'q** — ya'ni rad etilgan yozuvni ko'rish yoki qayta urinish imkoni yo'q.
3. **O'rniga holat ko'rsatkichi:** onlayn / yangilanmoqda / oflayn.

### Nega butunlay olib tashlab bo'lmaydi

Sizning to'rtinchi talabingizdan kelib chiqadi: internetsiz pul yozuvlari
bloklanadi. Kassir internetsiz qolganini **bilishi kerak**, aks holda "nega
sotuv qo'shilmayapti" deb turaveradi. Shuning uchun tugma o'rniga bosilmaydigan,
faqat ko'rsatadigan kichik holat belgisi qoladi.

---

## Bosqichlardan tashqari tuzatishlar

Bular reja bosqichi emas — ishlatish paytida chiqqan muammolar.

### Birinchi ishga tushirishda serverdagi nusxa olinadi

**Muammo.** Bir qurilma uzoq vaqt o'chiq turgan yoki eskicha ishlab kelgan
bo'lsa, unda boshqa qurilmalarda allaqachon o'chirilgan yozuvlar qolib
ketadi. O'chirish faqat *tombstone* bo'lib tarqaladi — o'sha xabarni
olmagan qurilmada qator joyida qolaveradi va keyingi "Yuborish" da qaytadan
serverga chiqadi. Ya'ni **o'chirish o'z-o'zini bekor qiladi**.

**Yechim.** Yangilanishdan keyingi **birinchi sinxronizatsiya bir tomonlama**
bo'ladi (`sync_service.reconcile_after_upgrade`):

| Serverda | Nima bo'ladi |
|---|---|
| Yaroqli (UUID) ma'lumot bor | **Serverdagi nusxa olinadi**, shu qurilmadagi eskisi o'rnini bo'shatadi. Eski nusxa zaxira fayl bo'lib saqlanadi. |
| Bo'sh yoki eski formatda | Bu qurilma yagona manba — u serverga yuklaydi |

Bu faqat **bir marta** ishlaydi: `sync_state.upgrade_reconcile_required`
belgisi migratsiyada qo'yiladi va birinchi muvaffaqiyatli sinxronizatsiyadan
keyin o'chadi. "Olish" ni ham, "Yuborish" ni ham bossangiz — natija bir xil.

**Qo'lda ham bor.** Sinxronizatsiya oynasida yangi tugma:
*"Shu qurilmani serverdagiga almashtirish"* (admin uchun). Bu mavjud
*"Serverni shu qurilmadagiga almashtirish"* tugmasining teskarisi. Ikkalasi
ham tasdiq so'raydi va o'chiriladigan tomonni avval zaxiraga oladi.

Testlar: `test_identity_reset_recovery.py` — jami 8 ta.

### "Olish" yangilangan qurilmada ishlamay qolgani

1-bosqichdagi himoya juda qattiq edi: qurilma UUID ga o'tgach "serverni
almashtirish kerak" belgisi qo'yilardi va **"Olish" o'sha belgining o'ziga
qarab** rad etardi — serverda nima borligini tekshirmasdan.

Endi qaror serverdan kelgan ma'lumotga qarab qabul qilinadi:

| Serverda | Natija |
|---|---|
| Eski (integer) yozuvlar | Aniq xabar: "Yuborish tugmasini bosing" |
| Boshqa qurilma yangilagan | Oddiy yuklab oladi, belgi o'chadi |
| Bo'sh | Yuklaydi, belgi o'chadi |

Bu bir vaqtning o'zida ikkinchi xavfni ham yopdi: ilgari ikkinchi yangilangan
qurilma birinchisining yuklaganini o'chirib yuborishi mumkin edi.
Testlar: `app/tests/test_identity_reset_recovery.py` (5 ta).

### Serverdagi boshqaruv paneliga kira olmaslik

Panel kodi soz — bulutda ishga tushirib tekshirildi (`/superadmin`,
`assets`, `login` — hammasi 200). Muammo serverdagi sozlamada:

| Sabab | Belgisi |
|---|---|
| `.env` da `SUPERADMIN_PASSWORD` bo'sh | Sahifa ochiladi, login ishlamaydi |
| `TRUSTED_HOSTS` da kirayotgan manzil yo'q | `400 Invalid host header` |

Ikkalasi ham inglizcha va tushunarsiz javob berardi. Endi xabarlar o'zbekcha,
va yangi `GET /api/v1/superadmin/availability` sahifa ochilishi bilanoq nima
yetishmayotganini yozadi. `.env.example` ga izohlar qo'shildi.
Testlar: `api/tests/test_superadmin_availability.py` (5 ta).

Panel manzili: `https://drinking-relight-trailside.ngrok-free.dev/superadmin`

---

## Ochiq qolgan narsalar va ogohlantirishlar

- ⚠️ **Dastur oynasi ochilib ko'rilmagan.** Faqat testlar (Qt testlari ham,
  lekin bulut muhitida — sizning mashinangizda `libEGL.so.1` yo'q).
- ⚠️ **Birinchi ishga tushirishda baza qayta quriladi** (ma'lumot saqlanadi,
  identifikatorlar almashadi). Zaxira nusxa `app/data/backups/` da avtomatik
  olinadi (`_backup_database_before_migration`).
- ⚠️ **Hamma qurilma bir vaqtda yangilanishi kerak.** Eski versiyadagi qurilma
  eski (integer) raqamlar bilan yozsa, u yozuvlar rad etiladi — yo'qolmaydi,
  lekin o'sha qurilma yangilanmaguncha ko'rinmaydi.
- `api/` testlari bu yerda ishlatilmadi (`fastapi` o'rnatilmagan). 1-bosqichda
  serverga **umuman tegilmadi**, shuning uchun ular o'zgarmagan.
- `currency` hali ham sinxronlanadi (siz shunday xohladingiz) — kelajakda
  "bir kassir USD ga o'tsa hammada o'zgardi" degan shikoyat chiqsa, sabab shu.

---

## Ish muhiti — amaliy eslatmalar

**Testlarni ishlatish** (loyiha ildizidan):

```
cd app
PYTHONPATH=.:tests python3 -m unittest discover -s tests -t tests
```

- Qt (interfeys) testlari uchun `QT_QPA_PLATFORM=offscreen` kerak
- Windows/WSL muhitida `libEGL.so.1` topilmasligi mumkin — unda Qt testlari
  ishlamaydi, faqat baza testlari ishlaydi
- `pytest` o'rnatilmagan, `unittest` ishlatiladi
- Bitta faylni ishlatish: `PYTHONPATH=.:tests python3 -m unittest tests.test_row_identity`

**Migratsiyani haqiqiy bazada sinash** — asl fayllarga tegmang, nusxa oling:

```
cp app/data/accounts/<account>/market_pos.db /tmp/sinov.db
python3 -c "import sys; sys.path.insert(0,'app'); import database as db; db.DB_PATH='/tmp/sinov.db'; print(db.run_migrations())"
```

**Reliz:** `app/version.py`, `packaging/installer.nsi`,
`packaging/version_info.txt` da versiyani ko'tarish → commit → annotatsiyali tag →
`git push --follow-tags` (tagsiz push qilinsa `build_release.yml` ishga tushmaydi).

---
