# NUTRIA Bot

Repository bot berisi FastAPI backend dan WhatsApp gateway. Backend menganalisis foto melalui OpenRouter dan menyimpan user, profil, whitelist, serta log makanan ke Cloud Firestore.

## Struktur

```text
bot/
├── backend/
├── whatsapp-gateway/
└── docker-compose.yml
```

## Menjalankan dengan Docker

1. Salin `.env.example` menjadi `.env`.
2. Simpan Firebase service-account JSON di `backend/key/firebase-service-account.json`.
3. Isi `OPENROUTER_API_KEY`, `FIREBASE_PROJECT_ID`, `FRONTEND_URL`, dan `ADMIN_PHONE_NUMBERS`.
4. Jalankan:

```powershell
docker compose up --build
```

`FRONTEND_URL` harus berisi URL deployment repository dashboard. Bot memakai URL ini ketika membuat tautan login admin.

Nilai Web App `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`, `FIREBASE_MESSAGING_SENDER_ID`, dan `FIREBASE_APP_ID` dapat memakai nilai yang sama di `.env` dashboard. Service-account dan `OPENROUTER_API_KEY` tetap hanya berada di bot.

## Development backend

```powershell
conda activate nutrition-ai
cd backend
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## Migrasi MySQL lama

Isi variabel `MYSQL_*` dan Firebase di `backend/.env`, lalu dari folder `backend` jalankan:

```powershell
conda run -n nutrition-ai python scripts/migrate_mysql_to_firestore.py
```

## Test

```powershell
cd backend
conda run -n nutrition-ai python -m unittest discover -s tests -v
```
