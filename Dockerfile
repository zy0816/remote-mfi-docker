FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUTOSSH_GATETIME=0

# x86-64/ARM64只安装运行库，不下载C/Rust工具链。
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update -o APT::Update::Error-Mode=any \
       -o Acquire::ForceIPv4=true -o Acquire::Retries=2 -o Acquire::https::Timeout=20 \
    && apt-get install -y --no-install-recommends \
       -o Acquire::ForceIPv4=true -o Acquire::Retries=2 -o Acquire::https::Timeout=20 \
       autossh libusb-1.0-0 libffi8 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
# 缺少wheel就失败，不在NAS上偷偷触发源码编译。ARMv7用专用配置。
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt
COPY mfi_service ./mfi_service
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
