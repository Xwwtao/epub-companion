# 单进程部署（spec §5.4）：数据全部落在挂载的 /data，配置全部走环境变量。
#   docker build -t epub-companion .
#   docker run -p 8000:8000 -v "$PWD/data:/data" --env-file .env epub-companion
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8000 \
    APP_ENV=production

WORKDIR /srv
# 先装依赖再拷代码，改代码时可以复用依赖这一层的缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 不用 root 运行
RUN useradd --create-home --uid 1000 reader && mkdir -p /data && chown reader /data
USER reader
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health')"

CMD ["python", "-m", "app"]
