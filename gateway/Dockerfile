FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wd_gateway/ ./wd_gateway/
COPY models.yaml .

EXPOSE 8002

CMD ["uvicorn", "wd_gateway.main:app", "--host", "0.0.0.0", "--port", "8002"]
