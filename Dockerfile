FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY downloader.py .

# /data  → state volume (token.json + downloaded.db)
# /output → .fit files volume
VOLUME ["/data", "/output"]

CMD ["python", "downloader.py"]
