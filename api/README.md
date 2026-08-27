# MarketStore POS API

PostgreSQL server lokal desktop app ma'lumotlarini user bo'yicha saqlash uchun ishlaydi. Desktop app asosiy biznes jarayonlarni lokal bajaradi, API esa credential bilan kelgan userning lokal jadval satrlarini `user_records` JSONB store ichida saqlaydi.

## Ishga tushirish

```bash
cp .env.example .env
docker compose up --build
```

`.env` maxfiy fayl hisoblanadi va Git'ga qo'shilmaydi. `SECRET_KEY` uchun kamida 32 baytli tasodifiy qiymat ishlating; Compose bu qiymat bo'lmasa API va workerni ishga tushirmaydi. `.env.example` faqat kalitlar va namuna qiymatlar uchun saqlanadi.

Lokal API: `http://127.0.0.1:8000`
Lokal Swagger: `http://127.0.0.1:8000/docs`

`http://` faqat lokal yoki yopiq test tarmog'i uchun. Internetga ochilgan serverda email, parol va bearer tokenlar uzatilgani sababli Nginx/Caddy orqali ishonchli TLS sertifikatli `https://` endpoint majburiy.

## Ngrok tunnel

Production serverda API host porti faqat `127.0.0.1` ga bog'langan. Ngrok
container esa API bilan Docker ichki tarmog'idagi `http://api:8000` manzili
orqali gaplashadi. Ngrok inspeksiya porti (`4040`) hostga chiqarilmaydi.

Ngrok Dashboard ichidan authtoken va accountga ajratilgan dev domainni oling,
so'ng serverdagi `api/.env` fayliga yozing:

```env
NGROK_AUTHTOKEN=your-secret-authtoken
NGROK_DOMAIN=https://drinking-relight-trailside.ngrok-free.dev
```

Tunnel profilini ishga tushiring:

```bash
docker compose --profile tunnel up --build -d
docker compose ps
docker compose logs --tail=100 ngrok
curl -i "${NGROK_DOMAIN}/health"
```

Server qayta ishga tushganda `restart: unless-stopped` tunnelni qayta ko'taradi.
Oddiy `docker compose up -d` buyrug'ida ham tunnel avtomatik tanlanishi uchun
serverdagi `.env` fayliga quyidagini qo'shish mumkin:

```env
COMPOSE_PROFILES=tunnel
```

Ngrok ishga tushganidan keyin Contabo firewall'dagi tashqi `8000/tcp` qoidasini
yoping. `NGROK_AUTHTOKEN` maxfiy bo'lib, Git repositoryga qo'shilmasligi kerak.
Desktop clientdagi `MARKETSTORE_API_URL` qiymati
`https://drinking-relight-trailside.ngrok-free.dev/api/v1` bo'lishi kerak.

Repo ichidagi test proxy alohida profile sifatida mavjud: `docker compose --profile proxy up --build -d`. U self-signed sertifikat yaratadi va faqat ulanishni sinash uchun; real productionda `api/nginx` konfiguratsiyasiga domen uchun ishonchli sertifikat mount qilinishi kerak.

Docker compose quyidagilarni ko'taradi:

- `postgres` - asosiy PostgreSQL baza
- `redis` - Celery queue va barcha API workerlar orasidagi realtime event bus
- `api` - FastAPI service
- `worker` - email yuboradigan Celery worker
- `ngrok` - `tunnel` profilidagi HTTPS tunnel

## Migration

```bash
alembic upgrade head
alembic revision --autogenerate -m "next change"
```

## Asosiy endpointlar

- `POST /api/v1/auth/register` - signup kodini emailga yuborish; account kod tasdiqlanguncha faol bo'lmaydi
- `POST /api/v1/auth/register/confirm` - 6 xonali kodni tasdiqlash va bearer token olish
- `POST /api/v1/auth/register/resend` - 3 daqiqa o'tgach yangi signup kodini yuborish
- `POST /api/v1/auth/token` - OAuth2 form orqali bearer token olish (`username` maydoniga email yuboriladi)
- `POST /api/v1/auth/login` - JSON email/parol orqali bearer token olish
- `POST /api/v1/auth/password-reset/request` - emailga verification code yuborish
- `POST /api/v1/auth/password-reset/confirm` - verification code bilan parolni yangilash
- `GET /api/v1/auth/me` - joriy user
- `POST /api/v1/sync/push` - ko'p jadval ma'lumotlarini saqlash
- `GET /api/v1/sync/pull` - user ma'lumotlarini qaytarish
- `PUT /api/v1/sync/tables/{table_name}/rows` - bitta jadval satrlarini saqlash
- `DELETE /api/v1/sync/tables/{table_name}/rows/{local_id}` - satrni serverda deleted deb belgilash
- `GET /api/v1/sync/summary` - user bo'yicha table statistikasi
- `GET /api/v1/sync/state` - accountning `generation` hisoblagichi va oxirgi o'zgarish ma'lumoti
- `GET /api/v1/sync/events` - Server-Sent Events oqimi (realtime o'zgarish xabari)
- `POST /api/v1/sync/reset` - accountning barcha server yozuvlarini o'chirish (to'liq qayta yuklash uchun)

## Realtime o'zgarish xabari

Har bir account uchun `sync_meta.generation` hisoblagichi saqlanadi. Istalgan qurilma
yozuv yuborsa hisoblagich 1 ga oshadi va o'sha accountning barcha ochiq
`GET /api/v1/sync/events` oqimlariga `change` eventi yuboriladi:

```
event: change
data: {"generation": 42, "tables": ["products"], "device_key": "desktop-ab12", "server_time": "..."}
```

Oqim ulanganda bir marta `hello` yuboradi, har 20 soniyada `ping` yuboradi (proxy tunnelni
yopib qo'ymasligi uchun). Qurilma uzilib qayta ulansa `?since_generation=N` bilan keladi va
o'tkazib yuborilgan o'zgarish `resumed: true` bilan qaytariladi.

Commitdan keyin xabar `SYNC_EVENTS_REDIS_URL` (`redis://redis:6379/2`) orqali barcha
API workerlarga tarqaladi. Shu sabab push so'rovi bir workerda, device SSE ulanishi
boshqa workerda bo'lsa ham signal darhol yetadi. Redis vaqtincha ishlamasa ma'lumot
yo'qolmaydi: PostgreSQL asosiy manba bo'lib qoladi va har bir SSE oqimi
`sync_meta.generation` ni 2 soniyada bir tekshirib, o'tkazib yuborilgan signalni tiklaydi.

Desktop signalni olgach `change_seq` cursoridan keyingi satrlarni incremental pull
qiladi. Offline qurilma qayta ulanganda `since_generation` orqali catch-up boshlanadi;
foydalanuvchi `Yuborish` yoki `Olish` tugmasini bosishi shart emas.

Nginx orqali ishlatilganda `/api/v1/sync/events` uchun `proxy_buffering off` kerak -
u `nginx/default.conf` da sozlangan.

## Conflict (Anki modeli)

`POST /api/v1/sync/push` ixtiyoriy `expected_generation` maydonini qabul qiladi. Agar
serverdagi qiymat boshqacha bo'lsa (ya'ni boshqa qurilma oraliqda yozgan bo'lsa) so'rov
`409` bilan rad etiladi:

```json
{"detail": {"code": "sync_conflict", "server_generation": 43, "expected_generation": 41}}
```

Desktop app bunda foydalanuvchiga "Serverdan yuklab olish" / "O'zimnikini yuborish"
oynasini ko'rsatadi. Ikkinchi variant `POST /api/v1/sync/reset` dan keyin to'liq snapshot
yuboradi. Har ikki holatda ham yo'qoladigan tomon avval `data/backups/` ga saqlanadi.

## Yangi release xabari (realtime)

Release chiqqanini server ikki yo'l bilan biladi:

1. **Actions ping (asosiy).** `build_release.yml` dagi `publish-release` job'i release
   yaratilgandan keyin `POST /api/v1/app/release-published` ga xabar yuboradi
   (`X-Release-Secret` sarlavhasi bilan). GitHub API'ga so'rov yo'q, kechikish ~0.
2. **Fon tekshiruvi (zaxira).** Server har `RELEASE_POLL_SECONDS` (default 600) da
   bir marta GitHub'dan so'raydi. Bu qo'lda yaratilgan release'ni yoki server
   o'chgan paytda kelgan ping'ni ushlab qoladi. Butun parkka soatiga 6 ta so'rov.

Ma'lumot `app_releases` jadvalida saqlanadi va barcha ochiq
`GET /api/v1/sync/events` oqimlariga `release` eventi sifatida tarqatiladi:

```
event: release
data: {"tag": "v1.0.5", "latest_version": "1.0.5", "name": "...", "published_at": "..."}
```

Oqim ochilganda `hello` ichida ham joriy release qaytadi, shuning uchun yopiq turgan
qurilma ulangan zahoti biladi. Har bir qurilma versiyani **o'zida** solishtiradi,
shuning uchun qurilmalar soni GitHub rate limitiga umuman ta'sir qilmaydi.

`GET /api/v1/app/version` endi avval `app_releases` jadvalidan javob beradi -
GitHub'ga so'rov ketmaydi. Jadval bo'sh bo'lsagina eski yo'l (cache -> GitHub API ->
redirect) ishlaydi.

### Kerakli sozlamalar

| Joy | Nomi | Izoh |
|---|---|---|
| `api/.env` | `RELEASE_PING_SECRET` | Maxfiy kalit. Bo'sh bo'lsa endpoint 503 qaytaradi. |
| `api/.env` | `GITHUB_TOKEN` | REST limitni 60/soatdan 5000/soatga ko'taradi. |
| Repo Secrets | `RELEASE_PING_SECRET` | `.env` dagi bilan bir xil qiymat. |
| Repo Secrets | `MARKETSTORE_API_URL` | Masalan `https://drinking-relight-trailside.ngrok-free.dev` (TLS sertifikati haqiqiy bo'lishi kerak). |

## Saqlash formati

Har lokal satr serverda quyidagicha saqlanadi:

```json
{
  "table_name": "products",
  "local_id": "15",
  "data": {"id": 15, "name": "Noutbuk", "stock": 3},
  "local_updated_at": "2026-08-02 19:30:00",
  "deleted_at": null,
  "source_device_key": "shop-1-pc"
}
```

`user_uid + table_name + local_id` unique. Shuning uchun har user o'z lokal bazasini alohida muhit sifatida saqlaydi va boshqa user bilan aralashmaydi. `user_id` ichki numeric id sifatida qoladi, API izolatsiya va sync amallarida stable `user_uid` ishlatiladi.

Desktop client `pull` ma'lumotlarini 1000 tadan sahifalab oladi. `push` bir so'rovda ko'pi bilan 1000 yozuv qabul qiladi va faqat ilovaning ruxsat etilgan lokal jadvallari saqlanadi.

Bearer token kerak bo'ladigan endpointlarda header:

```http
Authorization: Bearer <token>
```

## Email Sender

Signup va parol tiklash kodlari har bir userning o'z emailiga yuboriladi. `.env` ichidagi SMTP esa faqat yuboruvchi sender akkaunt bo'ladi. Minglab user bo'lsa ham userlar bitta emailga bog'lanmaydi, har biri o'z `users.email` qiymatiga code oladi.

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=sender@example.com
SMTP_PASSWORD=sender-smtp-password
SMTP_FROM_EMAIL=sender@example.com
SIGNUP_VERIFICATION_CODE_MINUTES=3
SIGNUP_VERIFICATION_RESEND_SECONDS=180
```

Gmail ishlatilsa oddiy parol emas, Google Account ichidan yaratilgan App Password kerak bo'ladi. Katta production uchun SendGrid, Mailgun, Amazon SES yoki korporativ SMTP ishlatish yaxshiroq.

Email yuborish request ichida bajarilmaydi. API verification code yaratib Redis queue'ga task tashlaydi, Celery worker esa SMTP orqali email yuboradi:

```text
FastAPI -> Redis -> Celery worker -> SMTP provider -> user email
```

## Account Login

Desktop appda user email va parol orqali ro'yxatdan o'tadi yoki login qiladi. Signup tugagach emaildagi 6 xonali kod tasdiqlanadi; kod 3 daqiqa amal qiladi. Tasdiqlangan yangi account `admin` sifatida ochiladi, desktop esa birinchi ekranda kassir rejimini ko'rsatadi.

Har bir accountga alohida `user_uid` beriladi va u `users.email` bilan bog'langan. Desktop app lokal ma'lumotni `data/accounts/email-<email_sha256>/market_pos.db` ichida saqlaydi; tokenli sync endpointlar esa faqat shu email accountining `user_uid` muhitidagi ma'lumotlarni qaytaradi. Lokal DB yo'qolsa u qayta yaratiladi va serverdan pull qilinadi. Server DB qayta yaralib UID almashsa, bir xil email lokal bazani topadi va uni yangi server muhitiga qayta yuboradi.
