FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || \
    pip install --no-cache-dir fastapi uvicorn httpx pydantic

COPY . .

EXPOSE 8000

# 默认启动控制台; 运行 demo 可覆盖 CMD:
#   docker run agentwarden-orchestrator python demo.py
CMD ["uvicorn", "console:app", "--host", "0.0.0.0", "--port", "8000"]
