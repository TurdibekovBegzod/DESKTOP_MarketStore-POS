# MarketStore POS API

PostgreSQL server lokal desktop app ma'lumotlarini user bo'yicha saqlash uchun ishlaydi. Desktop app asosiy biznes jarayonlarni lokal bajaradi, API esa credential bilan kelgan userning lokal jadval satrlarini `user_records` JSONB store ichida saqlaydi.

## Ishga tushirish

```bash
cp .env.example .env
docker compose up --build
```

API: `http://localhost:8000`
Swagger: `http://localhost:8000/docs`

Docker compose quyidagilarni ko'taradi:

- `postgres` - asosiy PostgreSQL baza
- `redis` - Celery broker/result backend
- `api` - FastAPI service
- `worker` - email yuboradigan Celery worker

## Migration

```bash
alembic upgrade head
alembic revision --autogenerate -m "next change"
```

## Asosiy endpointlar

- `POST /api/v1/auth/register` - email/parol bilan user yaratish. Har bir yangi account o'z muhitining `admin` useri bo'lib ochiladi.
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

Bearer token kerak bo'ladigan endpointlarda header:

```http
Authorization: Bearer <token>
```

## Email Sender

Parol tiklash kodi har bir userning o'z emailiga yuboriladi. `.env` ichidagi SMTP esa faqat yuboruvchi sender akkaunt bo'ladi. Minglab user bo'lsa ham userlar bitta emailga bog'lanmaydi, har biri o'z `users.email` qiymatiga code oladi.

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=sender@example.com
SMTP_PASSWORD=sender-smtp-password
SMTP_FROM_EMAIL=sender@example.com
```

Gmail ishlatilsa oddiy parol emas, Google Account ichidan yaratilgan App Password kerak bo'ladi. Katta production uchun SendGrid, Mailgun, Amazon SES yoki korporativ SMTP ishlatish yaxshiroq.

Email yuborish request ichida bajarilmaydi. API reset code yaratib Redis queue'ga task tashlaydi, Celery worker esa SMTP orqali email yuboradi:

```text
FastAPI -> Redis -> Celery worker -> SMTP provider -> user email
```

## Google Login

Desktop appdagi `Google orqali kirish` tugmasi API orqali OAuth server flow ishlatadi. Google Cloud Console ichida OAuth Client yarating va redirect URI sifatida quyidagini qo'shing:

```text
http://169.58.152.33:8000/api/v1/auth/google/callback
```

Keyin `.env` ichida quyidagilarni real qiymatlar bilan to'ldiring:

```env
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=http://169.58.152.33:8000/api/v1/auth/google/callback
```

Google login tugagach API userni email bo'yicha topadi yoki yangi user yaratadi, unga alohida `user_uid` beradi, desktop app esa bearer tokenni olib faqat shu `user_uid` muhitidagi ma'lumotlarni sync qiladi.
