FROM python:3.11-slim

WORKDIR /app

# Dipendenze minime di sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Crea cartella data se non presente
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
