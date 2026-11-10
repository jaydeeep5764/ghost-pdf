FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    fonts-liberation2 \
    fonts-dejavu-core \
    fonts-crosextra-carlito \
    && (apt-get install -y --no-install-recommends fonts-dancingscript || true) \
    && rm -rf /var/lib/apt/lists/*

# Run as non-root user (Hugging Face Spaces requirement)
RUN useradd -m -u 1000 user
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user . .

USER user
EXPOSE 7860

CMD ["python", "web/server.py"]
