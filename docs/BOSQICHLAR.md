# MarketStore POS — bosqichlar bo'yicha ish holati

> Bu fayl **qayerda to'xtaganimizni** yozib qo'yish uchun. Yangi sessiya ochilsa
> yoki oradan vaqt o'tsa, shu fayldan boshlang: nima qilingan, nega shunday
> qilingan, keyingi qadam nimadan boshlanadi.
>
> Oxirgi yangilanish: **2026-08-27** · Chiqarilgan versiya: **v1.1.5**
> · 2-bosqich va tuzatishlar hali commit qilinmagan

---

## Holat

**Jami 7 ta bosqich: 0 dan 6 gacha. Hammasi tugadi.**

| Bosqich | Mazmuni | Holat | Versiya | Taxminiy vaqt |
|---|---|---|---|---|
| **0** | Mavjud hisob-kitob xatolarini tuzatish | ✅ Tugadi, chiqarildi | `v1.1.3` | — |
| **1** | Yagona identifikator (UUID) | ✅ Tugadi, chiqarildi | `v1.1.5` | — |
| **2** | Pul raqamlarining asosi (o'zgarmas jurnal) | ✅ Tugadi | commit qilinmagan | — |
| **3** | Real-time (Telegram kabi) | ✅ Tugadi | commit qilinmagan | — |
| **4** | Eskirgan ma'lumotdan himoya | ✅ Tugadi | commit qilinmagan | — |
| **5** | Qurilmalararo bildirishnoma | ✅ Tugadi | commit qilinmagan | — |
| **6** | Sync tugmasini olib tashlash | ✅ Tugadi | commit qilinmagan | — |

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

## 4-bosqich — Eskirgan ma'lumotdan himoya ✅

Ikki kassir bir vaqtda ishlayapti. Biri mahsulotni sotdi. Ikkinchisining
ekranida hali eski qoldiq turibdi va u mahsulotni tahrirlab yubordi — natijada
sotuv qoldiqdan **yo'qolardi**.

### Nima qilindi

**Qurilma qaysi nusxadan ish qilayotganini aytadi.** Server har yozuv uchun
`sync_version` ni **doim** saqlab kelgan va uni har `/sync/pull` javobida
qaytargan — mijoz esa uni bir marta ham o'qimagan. Endi:

1. Yuklashda har qatorning versiyasi `sync_versions` jadvaliga yoziladi
2. O'zgartirish yuborilganda `expected_version` bilan ketadi — *"men ko'rgan
   nusxani o'zgartiryapman"*
3. Server solishtiradi: agar qator o'shandan beri o'zgargan bo'lsa — **o'sha
   bitta qator** rad etiladi

**409 emas, qisman muvaffaqiyat.** Butun to'plamni rad etish bitta eskirgan
mahsulot tahririni **muvaffaqiyatsiz sotuvga** aylantirardi. Endi qolgan
yozuvlar o'tadi, rad etilgani javobning `rejected` ro'yxatida qaytadi.

**Sotuv hech qachon rad etilmaydi.** Server ko'rmagan qator hech qanday
versiyaga ega emas, ya'ni u bilan tortishadigan narsa yo'q — shuning uchun
`expected_version` yuborilmaydi va qator so'zsiz qo'shiladi. Bu qoida
kodning tuzilishidan kelib chiqadi, alohida shart emas.

**Rad etilgandan keyin.** Mijoz darhol yuklab oladi (serverning nusxasi
o'rnatiladi) va foydalanuvchiga aytadi: *"Bu yozuv boshqa qurilmada o'zgargan
edi, shuning uchun sizning o'zgartirishingiz saqlanmadi. Yangi holat
ko'rsatildi."*

**Yuborilgandan keyin eslab qolingan versiya unutiladi** — u endi eskirgan,
keyingi yuklash yangisini beradi.

### Testlar

`app/tests/test_stale_write_protection.py` (6 ta) va
`api/tests/test_stale_write_protection.py` (5 ta):

- yuklangan har qatorning versiyasi eslab qolinishi
- o'zgartirish qaysi versiyadan qilinganini aytishi
- yangi qator hech qanday versiya da'vo qilmasligi *(sotuv rad etilmasligining sababi)*
- yuborilgandan keyin versiya unutilishi
- rad etilgan qator serverning nusxasi bilan almashtirilib, xabar berilishi
- to'plamning qolgan qismi baribir o'tishi
- serverda: versiya to'g'ri kelsa yozilishi, eskirgan bo'lsa rad etilishi,
  versiyasiz qator **doim** yozilishi

---

## 5-bosqich — Qurilmalararo bildirishnoma ✅

*"Sardor: Lenovo Ideapad sotdi"* — hamma qurilmada.

### Nima buzuq edi

`activity_logs` jadvali ancha oldingi migratsiyada yaratilgan va **unga hech
qachon hech narsa yozilmagan**. Faoliyat faqat Python ro'yxatida yashardi va
dastur yopilganda yo'qolardi — shuning uchun bir qurilma ikkinchisiga o'z
kassiri nima qilganini aytolmasdi. O'qilgan bildirishnomalar ham xotirada
edi, ya'ni dastur qayta ochilganda hammasi yana "o'qilmagan" bo'lib turardi.

### Nima qilindi

**Jadvalga uchta ustun qo'shildi** (`014_activity_feed`):

| Ustun | Nima uchun |
|---|---|
| `user_id` | kim qilgani |
| `user_name` | **ataylab takrorlangan** — foydalanuvchi o'chirilsa ham yozuv o'qilishi kerak |
| `device_key` | qaysi qurilmada — qurilma o'z ishini o'ziga xabar qilmasligi uchun |

**`log_activity` endi jadvalga ham yozadi.** **41 ta chaqiruv joyiga
tegilmadi** — ular nima bo'lganini aytadi, kim qilganini emas; shuning uchun
kirgan foydalanuvchi `db.set_activity_actor()` orqali beriladi. Yozish
"iloji boricha" rejimida: faoliyat yozuvi allaqachon sodir bo'lgan narsaning
tavsifi, uni yozolmaslik o'sha narsani bekor qilmasligi kerak.

**`activity_logs` `SYNC_TABLES` ga qo'shildi**, `users` dan keyin — u faqat
foydalanuvchiga ishora qiladi, tuple esa ota-ona birinchi tartibida.

**Jadval cheksiz o'smaydi** — oxirgi 500 ta yozuv qoladi
(`ACTIVITY_LOG_LIMIT`).

**Bildirishnoma bir marta chiqadi.** `take_new_remote_activities()` boshqa
qurilmalarda bo'lgan va hali ko'rsatilmagan yozuvlarni qaytaradi va o'sha
zahoti "ko'rildi" deb belgilaydi. `sync_state.activity_seen_at` — belgi.
Yuklashdan keyin `_on_sync_applied` ularni toast qilib ko'rsatadi:
sarlavha — kimligi, matn — nima qilgani.

**Eski tarix bir yo'la portlab ketmaydi.** Migratsiya `activity_seen_at` ni
o'sha payt bilan belgilaydi va mavjud yozuvlarga shu qurilmaning kalitini
qo'yadi — aks holda yangi versiyani birinchi ochganda bir oylik tarix toast
bo'lib yog'ilardi. *(Bu haqiqiy bazada chiqdi: `begzodasidev` da eski
buildan qolgan 3 ta yozuv bor ekan.)*

**O'qilganlar eslab qolinadi** — `notification_reads` jadvaliga yoziladi,
foydalanuvchi bo'yicha. **Sinxronlanmaydi**: bir kassir nimani o'qigani
ikkinchisiga aloqador emas.

**Tartib tuzatildi.** Bir soniya ichida bir nechta yozuv bo'lishi odatiy hol;
UUID bo'yicha ajratish ularni tasodifiy tartibda ko'rsatardi. Endi
`created_at` dan keyin `rowid` — ya'ni yozilgan tartibda.

### Testlar

`app/tests/test_activity_feed.py` (8 ta):

- yozuv dastur qayta ochilganda ham qolishi
- kim va qaysi qurilmada qilgani yozilishi
- yozuvlar boshqa qurilmalarga ketishi
- qurilma o'z ishini o'ziga xabar qilmasligi
- boshqa qurilmaniki **bir marta** e'lon qilinishi
- bizdan oldingi tarix birdan e'lon qilinmasligi
- o'qilganlar qayta ochilganda ham o'qilgan bo'lib qolishi va shaxsiy bo'lishi
- jadval cheksiz o'smasligi

Jami **177 ta test** o'tyapti.

---

## 6-bosqich — Sync tugmasini olib tashlash ✅

"Olish" va "Yuborish" tugmalari yo'q. O'rniga holat oynasi qoldi.

### Nima qilindi

**Butun baza darajasidagi konflikt tekshiruvi olib tashlandi.** Account bo'yicha
bitta hisoblagich (`generation`) *"men oxirgi qaraganimdan beri umuman nimadir
o'zgardimi"* deb so'raydi. Bu savol konflikt "ikki nusxadan birini tanlash"
degani bo'lganda to'g'ri edi. Endi har qatorning UUID si bor va o'zi qo'shiladi
— **ikki qurilmaning turli qatorlarni yozishi konflikt emas**. Avtomatik
sinxronizatsiya qator bo'yicha qo'shiladi; hisoblagich faqat qo'lda
almashtirish amallarida qoladi, chunki ular haqiqatan butun nusxani tanlaydi.

**`ConflictDialog` o'chirildi.** Uning o'rnida — agar baribir konflikt chiqsa —
xabar chiqadi va holat oynasiga yo'naltiradi.

**`sync_quarantine` jadvali.** Bu eng muhim qism: tugma bo'lmasa,
sinxronizatsiya **jimgina** to'xtab qolishi mumkin edi. Endi qo'llab
bo'lmagan yozuv **tashlanmaydi** — chetga olinadi, sababi bilan saqlanadi, har
yuklashda qayta urinib ko'riladi va holat oynasida ko'rinadi. Sababi bartaraf
bo'lgan zahoti (masalan uni tilga oluvchi sotuv kelganda) o'zi joyiga tushadi.

**O'qilgan joy belgisi endi to'xtamaydi.** Ilgari qo'llanmagan bitta yozuv
belgini muzlatib qo'yardi va undan keyingi hamma yuklash o'shanda tiqilib
qolardi. Karantin borligi uchun endi hech narsa yo'qolmaydi va belgi oldinga
suriladi.

**Holat oynasi:** sinxron / yuborilmagan o'zgarish bor / offline, yuborilmagan
sonlar, va karantindagi yozuvlar soni. Admin uchun ikkita tiklash amali
qoladi — ular sinxronizatsiya emas, ikki nusxadan birini tanlash.

### Nega tugma butunlay yo'qolmadi

Sizning to'rtinchi talabingizdan: internetsiz pul yozuvlari bloklanadi. Kassir
internetsiz qolganini **bilishi kerak**, aks holda "nega sotuv qo'shilmayapti"
deb turaveradi. Shuning uchun bosiladigan tugma emas, ko'rsatadigan belgi
qoldi.

### Testlar

`test_sync_dialog_permissions.py` yangilandi: kassir uchun bosadigan narsa
qolmagani, adminda ikkita tiklash amali borligi, kassir to'g'ridan-to'g'ri
chaqirsa ham rad etilishi. `test_auto_sync.py` ga karantin testlari qo'shildi:
qo'llab bo'lmagan yozuv **yo'qolmasligi**, va sababi bartaraf bo'lganda o'zi
joyiga tushishi.

Jami **186 ta desktop + 56 ta server testi** o'tyapti.

---

## Bosqichlardan tashqari tuzatishlar

Bular reja bosqichi emas — ishlatish paytida chiqqan muammolar.

### Yangilash o'rnatilmay, dastur o'chib qolardi

**Muammo.** Dastur ochiq turganda "Yangilash" bosilsa, yangi versiya yuklanardi,
keyin dastur o'chib ketardi va **o'rnatish bekor bo'lardi**.

**Sabab ikkita edi:**

1. `apply_and_restart()` o'rnatuvchini ishga tushirib, **o'sha zahoti**
   `app.quit()` chaqirardi. Lekin o'rnatuvchi administrator huquqini so'raydi
   (`RequestExecutionLevel admin`), ya'ni Windows ruxsat oynasini ko'rsatishi
   kerak. O'sha oyna **so'ragan jarayonga** tegishli. Dastur bir necha
   millisekunddan keyin yopilib, so'rovni o'ldirardi — natijada oyna
   ko'rinmasdan, o'rnatish boshlanmasdan hammasi tugardi.
2. `installer.nsi` da `taskkill /F /IM ... /T` — `/T` butun jarayon daraxtini
   o'ldiradi. Agar o'rnatuvchi qandaydir yo'l bilan dasturning bolasi bo'lib
   qolsa, u **o'zini ham** o'ldirardi.

**Yechim.** Dastur endi **umuman yopilmaydi**. O'rnatuvchining o'zi birinchi
qadamda dasturni yopadi — aynan fayllarni almashtirishga tayyor bo'lgan
paytda. Foydalanuvchi o'rnatuvchini bekor qilsa, dastur joyida qolaveradi
(ilgari yopilib ketardi va hech narsa o'rnatilmasdi).

`installer.nsi` da: `/T` olib tashlandi; avval **muloyim** `taskkill` (kassir
sotuvni tugatishi uchun), 1.5 soniya kutish, keyin majburiy `taskkill` va yana
1 soniya — fayl qulflari bo'shashi uchun.

Oynada endi aniq yozuv chiqadi: *"Windows ruxsat so'rasa, 'Ha' deb javob
bering. Dastur o'rnatish boshlanganda o'zi yopiladi — uni qo'lda yopmang."*
Tugma ham o'chiriladi, ikkinchi o'rnatuvchi ochilmasligi uchun.

Testlar: `test_updater.py` — dastur yopilmasligi, o'rnatuvchi hech qachon
dasturning bolasi bo'lmasligi, buzuq fayl umuman ishga tushirilmasligi.

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
