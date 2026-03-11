#!/bin/bash
# 启动Web聊天服务器的脚本

# 激活虚拟环境
source ./chat_env/bin/activate

# 运行服务器
echo "正在启动Web聊天服务器..."
echo "请在浏览器中访问: http://localhost:5000"
python web_server.py