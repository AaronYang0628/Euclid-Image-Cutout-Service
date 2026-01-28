#!/bin/bash
# Euclid Image Cutout Service - 开发环境启动脚本

set -e

echo "=========================================="
echo "🚀 Euclid Image Cutout Service - Dev Mode"
echo "=========================================="

# 检查必要的目录
echo "📁 检查目录..."
mkdir -p /workspace/outputs /workspace/cache /workspace/tmp /workspace/data

# 检查配置文件
if [ ! -f "/workspace/config.yaml" ]; then
    echo "⚠️  警告: config.yaml 不存在"
fi

# 显示服务信息
echo ""
echo "📡 服务端口:"
echo "   - Flask App: http://localhost:5000"
echo "   - MCP SSE Server: http://localhost:8000"
echo ""
echo "🛠️  开发工具:"
echo "   - black (格式化): black ."
echo "   - flake8 (检查): flake8 ."
echo "   - pytest (测试): pytest"
echo ""
echo "🚀 启动服务:"
echo "   - Flask: python run_flask.py"
echo "   - MCP: python run_mcp_sse.py"
echo "   - 同时启动: ./start.sh"
echo ""
echo "=========================================="
echo "✅ 开发环境准备就绪!"
echo "=========================================="

# 保持容器运行
exec "$@"
