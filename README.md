# HEICFlow — public HEIC converter with ads + Pro quality

Flask web app to convert HEIC/HEIF images to JPG, PNG and WEBP.

This version is prepared for the model:

- Guest conversion without mandatory login
- Free standard quality with ads and daily limits
- Google login for a free monthly quota
- Pro plans for high/maximum quality, larger batches and no ads
- Anonymous abuse protection using cookie + hashed IP/fingerprint counters
- SEO pages for HEIC to JPG/PNG/WEBP
- Legal pages: privacy, terms, contact, about
- AdSense placeholders through environment variables

## Important security note

Do not commit `.env`. Only commit `.env.example`.
If a real `.env` was ever shared or uploaded, rotate those credentials before production.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open:

```text
http://localhost:8000
```

## Docker run

```bash
docker build -t heicflow .
docker run --rm -p 8000:8000 --env-file .env heicflow
```

## Production notes

Set these values in Azure App Service / Container Apps configuration, not inside the repository:

- SECRET_KEY
- PUBLIC_BASE_URL
- CONTACT_EMAIL
- DATABASE_URL
- ADSENSE_CLIENT and ad slots after AdSense approval
- GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET if using login
- Paddle/ePayco keys if using paid plans

For Azure App Service Container:

```text
WEBSITES_PORT=8000
PUBLIC_BASE_URL=https://yourapp.azurewebsites.net
```

## Recommended first production configuration

- Keep guest batch small: 5 files
- Keep max file size around 10 MB while validating traffic
- Use one Gunicorn worker with threads to avoid high memory usage
- Move to PostgreSQL before relying on accounts/payments
- Put Cloudflare/Azure Front Door in front if traffic grows

## Routes

- `/` main converter
- `/heic-to-jpg`
- `/heic-to-png`
- `/heic-to-webp`
- `/pricing`
- `/privacy`
- `/terms`
- `/contact`
- `/about`
- `/robots.txt`
- `/sitemap.xml`
