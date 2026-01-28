# Euclid Image Cutout Service - Development Container
# 基于项目的 Dockerfile，添加开发工具

FROM m.daocloud.io/docker.io/library/python:3.12-slim

# 设置工作目录
WORKDIR /workspace

# 安装系统依赖和开发工具
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    vim \
    nano \
    htop \
    tree \
    build-essential \
    ca-certificates \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# 安装 Node.js (使用清华镜像源)
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/nodesource/deb_20.x/nodistro/repo.gpg | gpg --dearmor -o /usr/share/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://mirrors.tuna.tsinghua.edu.cn/nodesource/deb_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    npm config set registry https://registry.npmmirror.com && \
    node --version && npm --version

# 复制依赖文件
COPY requirements.txt .

# 配置 pip 使用国内镜像源并安装 Python 依赖
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    sse-starlette \
    mcp

# 安装开发工具
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && \
    pip install --no-cache-dir \
    black \
    flake8 \
    isort \
    pytest \
    pytest-cov \
    ipython \
    ipdb \
    autopep8 \
    pylint

# 创建必要的目录
RUN mkdir -p /workspace/outputs /workspace/cache /workspace/tmp /workspace/data /workspace/templates /workspace/static

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace
ENV FLASK_ENV=development
ENV FLASK_DEBUG=1

# 暴露端口
EXPOSE 5000 8000

# 默认命令（开发模式下通常不自动启动服务）
CMD ["/bin/bash"]
