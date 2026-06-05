FROM python:3.12-slim

# System deps for HEIC/HEIF via libheif
RUN apt-get update && apt-get install -y --no-install-recommends \
    libheif-dev \
    build-essential \
    pkg-config \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000
EXPOSE 8000

CMD ["gunicorn", "-b", "0.0.0.0:8000", "wsgi:app", "--workers", "1", "--threads", "4", "--timeout", "120"]
