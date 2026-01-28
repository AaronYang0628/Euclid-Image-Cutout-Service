# Euclid Image Cutout Service - Development Environment

## 快速开始

### 1. 使用 VS Code Dev Container

1. 安装 VS Code 和 Docker
2. 安装 VS Code 扩展：`Remote - Containers`
3. 打开项目文件夹
4. 按 `F1`，选择 `Remote-Containers: Reopen in Container`
5. 等待容器构建完成

### 2. 启动服务

在容器内的终端中运行：

```bash
# 启动 Flask 应用
python run_flask.py

# 或在另一个终端启动 MCP 服务
python run_mcp_sse.py

# 或同时启动两个服务
./start.sh
```

### 3. 访问服务

- Flask Web UI: http://localhost:5000
- MCP SSE Server: http://localhost:8000

## 开发工具

容器已预装以下开发工具：

- **Python 工具**：
  - black (代码格式化)
  - flake8 (代码检查)
  - isort (导入排序)
  - pytest (测试框架)
  - ipython (交互式 Python)
  - ipdb (调试器)

- **VS Code 扩展**：
  - Python
  - Pylance
  - Black Formatter
  - Flake8
  - Jupyter
  - Docker
  - GitLens

## 配置说明

### 数据目录挂载

如果需要访问本地的 Euclid 数据，编辑 `.devcontainer/devcontainer.json`：

```json
"runArgs": [
  "-v", "/path/to/your/euclid/data:/data/euclid"
]
```

然后更新 `config.yaml` 中的数据路径：

```yaml
data:
  root: "/data/euclid"
```

### GPU 支持

如果需要 GPU 支持（用于 PyTorch），取消 `devcontainer.json` 中的注释：

```json
"runArgs": [
  "--gpus=all"
]
```

确保主机已安装 NVIDIA Docker 支持。

### 环境变量

可以在 `devcontainer.json` 的 `containerEnv` 中添加环境变量：

```json
"containerEnv": {
  "CUSTOM_VAR": "value"
}
```

## 常用命令

```bash
# 运行测试
pytest

# 代码格式化
black .

# 代码检查
flake8 .

# 导入排序
isort .

# 查看日志
tail -f ~/euclid_logs/euclid_service.log

# 清理缓存
rm -rf cache/* tmp/* outputs/*
```

## 故障排除

### 端口冲突

如果端口 5000 或 8000 已被占用，修改 `devcontainer.json` 中的端口映射：

```json
"forwardPorts": [5001, 8001]
```

并相应更新 `config.yaml` 中的端口配置。

### 权限问题

如果遇到文件权限问题，可以在容器内运行：

```bash
chown -R root:root /workspace
```

### 重建容器

如果需要重建容器：

1. 按 `F1`
2. 选择 `Remote-Containers: Rebuild Container`

## 项目结构

```
.
├── .devcontainer/          # Dev Container 配置
│   ├── devcontainer.json   # 容器配置
│   ├── Dockerfile          # 开发环境镜像
│   └── README.md           # 本文件
├── euclid_service/         # 核心服务代码
├── flask_app/              # Flask 应用
├── templates/              # HTML 模板
├── config.yaml             # 配置文件
├── requirements.txt        # Python 依赖
└── run_flask.py            # Flask 启动脚本
```

## 贡献指南

1. 代码提交前运行 `black` 和 `flake8`
2. 确保所有测试通过：`pytest`
3. 更新相关文档
4. 提交 Pull Request

## 支持

如有问题，请查看项目 README 或提交 Issue。
