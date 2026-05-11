# IntelliStock Backend
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m spacy download en_core_web_lg

# Copy backend application
COPY . .

# Pre-train the CandlesStrategy model so backtests load weights from disk
# instead of retraining (~30s, 99% CPU) on every run.
RUN python strategies/pretrain_candles.py

# Expose Socket.IO / WSGI server port (backend) and API port (api service)
EXPOSE 5000 8000

# Run the main server
CMD ["python", "server.py"]
