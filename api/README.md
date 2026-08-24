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
NGROK_DOMAIN=https://your-domain.ngrok-free.app
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
`https://your-domain.ngrok-free.app/api/v1` bo'lishi kerak.

Repo ichidagi test proxy alohida profile sifatida mavjud: `docker compose --profile proxy up --build -d`. U self-signed sertifikat yaratadi va faqat ulanishni sinash uchun; real productionda `api/nginx` konfiguratsiyasiga domen uchun ishonchli sertifikat mount qilinishi kerak.

Docker compose quyidagilarni ko'taradi:

- `postgres` - asosiy PostgreSQL baza
- `redis` - Celery broker/result backend
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
