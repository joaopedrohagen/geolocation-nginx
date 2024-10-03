FROM python:3.10.12-slim-bookworm

RUN apt-get update && apt-get install -y\
    build-essential \
    libpq-dev \
    python3-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements.txt
COPY ip_nginx.py ip_nginx.py

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python3", "ip_nginx.py"]
