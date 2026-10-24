FROM docker.m.daocloud.io/library/python:3.12-slim-bookworm

# 注意：这个基础镜像的 apt 源密钥已过期（Debian 轮换了 archive keyring），
# apt-get update 会因 NO_PUBKEY 失败。所以镜像里不装 tzdata，
# 时区改由 docker run 挂载宿主机 /usr/share/zoneinfo + TZ 环境变量提供。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_PROGRESS_BAR=off \
    TZ=Asia/Shanghai

WORKDIR /app

# --progress-bar off：这个基础镜像里 pip 的 rich 进度条会因起不了线程而崩溃
COPY requirements-web.txt ./
RUN pip install --no-cache-dir --progress-bar off -r requirements-web.txt

COPY . .

# 容器内监听 0.0.0.0，但宿主机只把端口发布到 127.0.0.1，
# 外部只能经云端 nginx / FRP 进来，不直接暴露。
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.web.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
