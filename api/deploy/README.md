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
cd /root/DESKTOP_MarketStore-POS   # yoki repo qayerda bo'lsa
git remote -v && git branch --show-current    # origin GitHub, branch main bo'lsin
pwd                                            # bu yo'lni DEPLOY_PATH ga yozasiz
```

`api/.env` serverda turishi shart (Git'ga kirmaydi). Repo private bo'lsa,
serverga read-only deploy key qo'shing, aks holda `git fetch` parol so'raydi.

### 2. Deploy uchun SSH kalit yarating

**O'z kompyuteringizda** (serverda emas).

#### Windows (CMD)

`~` bu yerda ishlamaydi va `.ssh` papkasi bo'lmasligi mumkin, shuning uchun
avval uni yarating:

```bat
mkdir "%USERPROFILE%\.ssh"
ssh-keygen -t ed25519 -C "github-actions-deploy" -f "%USERPROFILE%\.ssh\marketstore_deploy" -N ""
```

Ochiq kalitni serverga qo'shing (Windows'da `ssh-copy-id` yo'q):

```bat
type "%USERPROFILE%\.ssh\marketstore_deploy.pub" | ssh <user>@<server> "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Kalit ishlashini tekshiring (parol so'ramasligi kerak):

```bat
ssh -i "%USERPROFILE%\.ssh\marketstore_deploy" <user>@<server> "echo ULANDI"
```

`DEPLOY_SSH_KEY` secretiga qo'yiladigan **yopiq** kalitni ko'chirish uchun:

```bat
type "%USERPROFILE%\.ssh\marketstore_deploy"
```

`-----BEGIN OPENSSH PRIVATE KEY-----` dan `-----END OPENSSH PRIVATE KEY-----`
gacha, oxirgi bo'sh qatori bilan birga to'liq nusxalang. `.pub` fayl emas.

`DEPLOY_KNOWN_HOSTS` uchun:

```bat
ssh-keyscan -H <server>
```

#### Linux / macOS

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/marketstore_deploy -N ""
ssh-copy-id -i ~/.ssh/marketstore_deploy.pub <user>@<server>
cat ~/.ssh/marketstore_deploy     # DEPLOY_SSH_KEY
ssh-keyscan -H <server>           # DEPLOY_KNOWN_HOSTS
```

### 3. GitHub secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Qiymati | Majburiy |
|---|---|---|
| `DEPLOY_HOST` | server IP yoki domen | ha |
| `DEPLOY_USER` | SSH useri (`root`, `ubuntu`, ...) | ha |
| `DEPLOY_SSH_KEY` | `~/.ssh/marketstore_deploy` ning **to'liq** tarkibi (`-----BEGIN` dan `-----END` gacha) | ha |
| `DEPLOY_PATH` | serverdagi repo yo'li | yo'q (default `/root/DESKTOP_MarketStore-POS`) |
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
cd /root/DESKTOP_MarketStore-POS
bash api/deploy/deploy.sh
```

Sozlanadigan qiymatlar: `DEPLOY_BRANCH` (default `main`), `DEPLOY_FORCE=1`
(o'zgarish bo'lmasa ham qursin), `DEPLOY_HEALTH_URL`, `DEPLOY_HEALTH_RETRIES`.

## Tez-tez uchraydigan xatolar

| Xato | Sababi |
|---|---|
| `Permission denied (publickey)` | `DEPLOY_SSH_KEY` yopiq kalit emas (`.pub` qo'yilgan), yoki serverdagi `authorized_keys` ga qo'shilmagan |
| `Saving key "~/.ssh/..." failed: No such file or directory` | Windows CMD `~` ni tushunmaydi — `%USERPROFILE%\.ssh\...` yozing va papkani `mkdir` bilan oldin yarating |
| `Load key ...: error in libcrypto` | Secretga kalit yarim ko'chirilgan — `BEGIN`/`END` qatorlari bilan to'liq qo'ying |
| `Host key verification failed` | `DEPLOY_KNOWN_HOSTS` eskirgan — `ssh-keyscan` ni qayta oling |
| `api/.env topilmadi` | `DEPLOY_PATH` noto'g'ri, yoki serverda `.env` yaratilmagan |
| `API ... javob bermadi` | Yangi build ishga tushmadi; skript o'zi orqaga qaytargan, logni Actions ichidan o'qing |
