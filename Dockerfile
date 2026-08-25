FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8096
# network_mode:host => bind all interfaces (LAN + Tailscale reachability)
CMD ["uvicorn", "app:app", "--host", "[IP_ADDRESS]", "--port", "8096"]
