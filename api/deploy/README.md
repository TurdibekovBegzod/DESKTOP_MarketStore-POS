# API deploy (CI/CD)

`main` ga har push — agar `api/**` o'zgargan bo'lsa — testdan o'tadi va serverga
o'zi chiqadi. GitLab kerak emas, hammasi GitHub Actions ichida.

```
push main ──> Tests (api 41 + app 65) ──> SSH ──> deploy.sh ──> health check
                     │                                              │
                  yiqilsa                                      javob bermasa
                     ▼                                              ▼
              deploy bo'lmaydi                        eski commitga qaytariladi
```

Ishlatiladigan fayllar:

- `.github/workflows/deploy_api.yml` — testlar va SSH orqali chaqiruv
- `api/deploy/deploy.sh` — serverda bajariladigan qism (qo'lda ham ishlatsa bo'ladi)

## Nima uchun konteynerlar o'chirilmaydi

`deploy.sh` hech qachon `docker compose down` qilmaydi. `up -d --build` faqat
image'i yoki konfiguratsiyasi o'zgargan konteynerni almashtiradi, shuning uchun
`postgres` va `redis` — va ularning volume'lari — umuman tegilmaydi. Migratsiya
`api` konteynerining CMD'idagi `alembic upgrade head` orqali o'zi ishlaydi.

Yangi build health check'dan o'tmasa, skript avvalgi commitga qaytarib qayta
ko'taradi va CI qizil bo'ladi — do'konlar ishlayveradi.

## Bir martalik sozlash

### 1. Serverda repo turgan joyni aniqlang

```bash
ssh <user>@<server>
cd /opt/marketstore/DESKTOP_MarketStore-POS   # yoki repo qayerda bo'lsa
git remote -v && git branch --show-current    # origin GitHub, branch main bo'lsin
pwd                                            # bu yo'lni DEPLOY_PATH ga yozasiz
```

`api/.env` serverda turishi shart (Git'ga kirmaydi). Repo private bo'lsa,
serverga read-only deploy key qo'shing, aks holda `git fetch` parol so'raydi.

### 2. Deploy uchun SSH kalit yarating

**O'z kompyuteringizda** (serverda emas):

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/marketstore_deploy -N ""
```

Ochiq qismini serverga qo'shing:

```bash
ssh-copy-id -i ~/.ssh/marketstore_deploy.pub <user>@<server>
# yoki qo'lda: server ~/.ssh/authorized_keys fayliga .pub tarkibini qo'shing
```

Server kalitini pin qilish uchun (majburiy emas, lekin tavsiya etiladi):

```bash
ssh-keyscan -H <server> 
```

### 3. GitHub secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Qiymati | Majburiy |
|---|---|---|
| `DEPLOY_HOST` | server IP yoki domen | ha |
| `DEPLOY_USER` | SSH useri (`root`, `ubuntu`, ...) | ha |
| `DEPLOY_SSH_KEY` | `~/.ssh/marketstore_deploy` ning **to'liq** tarkibi (`-----BEGIN` dan `-----END` gacha) | ha |
| `DEPLOY_PATH` | serverdagi repo yo'li | yo'q (default `/opt/marketstore/DESKTOP_MarketStore-POS`) |
| `DEPLOY_PORT` | SSH porti | yo'q (default `22`) |
| `DEPLOY_KNOWN_HOSTS` | `ssh-keyscan -H <server>` natijasi | yo'q, lekin tavsiya etiladi |
| `MARKETSTORE_API_URL` | ngrok manzili — deploydan keyin tashqaridan tekshiriladi | yo'q |

Secretlar qo'yilmasa workflow yiqilmaydi, faqat deploy bosqichini o'tkazib
yuboradi va sababini logda yozadi.

### 4. Sinab ko'ring

Actions → **Deploy API** → **Run workflow** (`force: true` bilan, server allaqachon
shu commitda bo'lsa ham qursin).

## Qo'lda deploy

CI ishlamay qolsa, serverda o'sha skriptning o'zi:

```bash
cd /opt/marketstore/DESKTOP_MarketStore-POS
bash api/deploy/deploy.sh
```

Sozlanadigan qiymatlar: `DEPLOY_BRANCH` (default `main`), `DEPLOY_FORCE=1`
(o'zgarish bo'lmasa ham qursin), `DEPLOY_HEALTH_URL`, `DEPLOY_HEALTH_RETRIES`.

## Tez-tez uchraydigan xatolar

| Xato | Sababi |
|---|---|
| `Permission denied (publickey)` | `DEPLOY_SSH_KEY` yopiq kalit emas (`.pub` qo'yilgan), yoki serverdagi `authorized_keys` ga qo'shilmagan |
| `Host key verification failed` | `DEPLOY_KNOWN_HOSTS` eskirgan — `ssh-keyscan` ni qayta oling |
| `api/.env topilmadi` | `DEPLOY_PATH` noto'g'ri, yoki serverda `.env` yaratilmagan |
| `API ... javob bermadi` | Yangi build ishga tushmadi; skript o'zi orqaga qaytargan, logni Actions ichidan o'qing |
