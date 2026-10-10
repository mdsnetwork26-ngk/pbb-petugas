# Aplikasi Loket Pembayaran PBB-P2 — jalankan tanpa menyentuh Python host.
# Cocok untuk server lama (mis. Ubuntu 20.04): Python 3.13 dibawa kontainer.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Jakarta \
    PBB_PORT=8088

# tzdata supaya TZ di atas benar-benar dipakai (tanggal kwitansi & hitung denda)
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data/backup data/upload data/auto/sudah-diproses data/cetak

EXPOSE 8088
CMD ["python", "app.py"]
