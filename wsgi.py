#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Euclid Flash App - WSGI 入口文件
为 Gunicorn 提供标准化的启动接口
"""

import os
import sys

# 将当前目录添加到 Python 路径，确保所有模块都能正确导入
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# 设置环境变量，确保应用知道自己在生产环境运行
os.environ.setdefault('FLASK_ENV', 'production')
os.environ.setdefault('PYTHONPATH', current_dir)

# 导入你的 Flask 应用实例
# 注意：这里假设你的主文件中 Flask 实例名为 'app'
# 如果你的实例名不同（如 'application'），请相应修改
try:
    from Euclid_flash_app import app
    print(f"✅ 成功从 {__file__} 导入 Flask 应用")
except ImportError as e:
    print(f"❌ 导入失败: {e}")
    print(f"当前目录: {current_dir}")
    print(f"Python路径: {sys.path}")
    raise

# Gunicorn 需要这个 'application' 变量（或者你在配置中指定的变量名）
# 这里我们导出 'app'，但也可以重命名为 'application'
application = app

if __name__ == '__main__':
    # 仅当直接运行此脚本时启动开发服务器
    print("启动开发服务器（仅用于测试）...")
    app.run(host='0.0.0.0', port=5000, debug=False)