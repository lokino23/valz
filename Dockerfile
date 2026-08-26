FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8102
# network_mode:host => 0.0.0.0 binds all interfaces (LAN + Tailscale reachability)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8102"]
