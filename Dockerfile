FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=7860

# Hugging Face Spaces runs containers as a non-root user (uid 1000)
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .
USER user

EXPOSE 7860
# One worker (jobs are kept in memory) with threads for concurrent requests
CMD gunicorn app:app --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 300
