FROM python:3.12-slim

WORKDIR /app

# Install deps in a separate layer from the app code so Docker only
# reinstalls packages when requirements.txt actually changes, not on every
# code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rate_limiter/ ./rate_limiter/

EXPOSE 8000

CMD ["uvicorn", "rate_limiter.main:app", "--host", "0.0.0.0", "--port", "8000"]
