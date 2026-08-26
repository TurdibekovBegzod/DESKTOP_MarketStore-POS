# Monitoring (Grafana Cloud)

Bitta konteyner — Grafana Alloy — hammasini yig'ib Grafana Cloud'ga yuboradi.
Serverda Prometheus ham, Grafana ham o'rnatilmaydi.

| Nima | Qayerdan | Grafana'da nima ko'rinadi |
|---|---|---|
| Konteyner CPU/RAM/tarmoq, **qayta yonib-o'chgani** | cAdvisor (Alloy ichida) | Qaysi konteyner qachon restart bo'lgan, nechchi marta |
| Server diski, RAM, CPU, load | node exporter (Alloy ichida) | Postgres diski to'lishidan oldin ko'rinadi |
| API so'rovlari | API'ning `/metrics` endpointi | Soniyasiga so'rov, javob vaqti, 4xx/5xx, endpoint bo'yicha |
| Barcha konteyner loglari | Docker log driver -> Loki | Bitta joyda, qidiriladigan |

Fayllar: `alloy-config.alloy` (agent konfiguratsiyasi), `docker-compose.yml`
ichidagi `alloy` servisi (`monitoring` profilida).

## `/metrics` nega token bilan yopilgan

Ngrok tunneli **hamma** yo'lni tashqariga chiqaradi. Ochiq `/metrics` bo'lsa,
manzilni topgan har kim qaysi endpointlaringiz bor, kuniga nechta so'rov
kelishi, qachon xato ko'payishini ko'ra oladi. Shuning uchun endpoint faqat
`METRICS_TOKEN` ni ko'rsatgan chaqiruvchiga javob beradi; token qo'yilmasa
endpoint umuman yaratilmaydi (404).

Alloy API bilan Docker ichki tarmog'i orqali (`api:8000`) gaplashadi —
tunnel orqali emas.

## Sozlash

### 1. Grafana Cloud'da hisob oching

<https://grafana.com/auth/sign-up/create-user> — bepul tier: 10k metrika seriyasi,
50 GB log, 14 kun tarix. Karta so'ralmaydi.

### 2. Ulanish ma'lumotlarini oling

Grafana Cloud portalida: **Connections → Add new connection → Hosted Prometheus metrics**

- `GRAFANA_PROM_URL` — "Remote Write Endpoint" (`.../api/prom/push` bilan tugaydi)
- `GRAFANA_PROM_USER` — "Username / Instance ID" (raqam)

So'ng **Hosted Logs**:

- `GRAFANA_LOKI_URL` — (`.../loki/api/v1/push` bilan tugaydi)
- `GRAFANA_LOKI_USER` — raqam (Prometheus'nikidan boshqa)

Token: **Access Policies → Create access policy**, ruxsatlar `metrics:write` va
`logs:write`. Chiqqan tokenni `GRAFANA_CLOUD_TOKEN` ga yozasiz — ikkalasi uchun
bitta token yetadi.

### 3. Serverdagi `api/.env` ga yozing

```bash
ssh root@<server>
cd /root/DESKTOP_MarketStore-POS/api

# Metrika tokenini o'zingiz yaratasiz - bu Grafana'niki emas:
openssl rand -hex 32

nano .env
```

```env
METRICS_TOKEN=<yuqoridagi 64 belgili qiymat>
MONITOR_INSTANCE=marketstore-prod
GRAFANA_PROM_URL=https://prometheus-prod-XX-prod-eu-west-0.grafana.net/api/prom/push
GRAFANA_PROM_USER=1234567
GRAFANA_LOKI_URL=https://logs-prod-XXX.grafana.net/loki/api/v1/push
GRAFANA_LOKI_USER=1234567
GRAFANA_CLOUD_TOKEN=glc_eyJ...
```

`monitoring` profili doim yonib tursin desangiz, o'sha `.env` ga:

```env
COMPOSE_PROFILES=tunnel,monitoring
```

### 4. Ishga tushiring

```bash
cd /root/DESKTOP_MarketStore-POS/api
docker compose --profile monitoring up -d --build
docker compose logs --tail=50 alloy
```

Loglarda xato bo'lmasa 1-2 daqiqada Grafana Cloud'da ma'lumot paydo bo'ladi.

### 5. Tekshirish

```bash
# /metrics ichkaridan ishlayaptimi
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics | head

# tashqaridan yopiqmi (401 chiqishi kerak, 200 emas)
curl -s -o /dev/null -w "%{http_code}\n" https://<ngrok-domen>/metrics

# Alloy o'zining holati
curl -s http://127.0.0.1:12345/ready
```

## Grafana'da nimadan boshlash

Grafana Cloud → **Dashboards → New → Import**, quyidagi ID'larni kiriting:

| ID | Nima |
|---|---|
| `893` | Docker konteynerlari (cAdvisor) |
| `1860` | Node Exporter Full — server holati |

API uchun tayyor dashboard yo'q, lekin bu so'rovlar bilan o'zingiz yasaysiz
(**Explore → Metrics**):

```promql
# soniyasiga so'rov, endpoint bo'yicha
sum by (handler) (rate(http_requests_total[5m]))

# xatolar ulushi
sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))

# 95-foizli javob vaqti
histogram_quantile(0.95, sum by (le, handler) (rate(http_request_duration_seconds_bucket[5m])))

# konteyner oxirgi marta qachon ko'tarilgan (restartni shundan ko'rasiz)
time() - container_start_time_seconds{name=~"marketstore-.*"}
```

Loglar: **Explore → Logs**, so'rov `{container="marketstore-api"}`.

## Sarf

Alloy ~150-250 MB RAM oladi. Grafana Cloud bepul tieriga bu hajm bemalol
sig'adi. Kerak bo'lmasa `--profile monitoring` siz ishga tushiring — qolgan
servislarga umuman ta'sir qilmaydi.
