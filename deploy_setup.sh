#!/bin/bash

# 局域网聊天系统部署脚本
# 用于在4台电脑上快速设置环境

echo "=== 局域网聊天系统部署工具 ==="
echo "请选择角色："
echo "1) 服务器 (Server)"
echo "2) 客户端 (Client)"
echo ""

read -p "请输入选择 (1 或 2): " choice

if [ "$choice" = "1" ]; then
    echo "正在配置服务器..."
    
    # 检查依赖
    if ! command -v gcc &> /dev/null; then
        echo "安装编译工具..."
        sudo apt update && sudo apt install -y build-essential libncurses-dev
    fi
    
    # 编译服务器
    if [ -f "Makefile" ]; then
        make server
        echo ""
        echo "✅ 服务器配置完成！"
        echo "运行命令: ./server"
        echo ""
        echo "服务器启动后，请记录显示的IP地址，客户端需要使用此IP连接。"
    else
        echo "❌ 错误：未找到 Makefile 文件"
        exit 1
    fi
    
elif [ "$choice" = "2" ]; then
    echo "正在配置客户端..."
    
    # 检查依赖
    if ! command -v gcc &> /dev/null; then
        echo "安装编译工具..."
        sudo apt update && sudo apt install -y build-essential libncurses-dev
    fi
    
    # 编译客户端
    if [ -f "Makefile" ]; then
        make client
        echo ""
        echo "✅ 客户端配置完成！"
        echo "运行命令: ./client"
        echo ""
        echo "启动时需要输入服务器IP地址（从服务器获取）和用户名。"
    else
        echo "❌ 错误：未找到 Makefile 文件"
        exit 1
    fi
    
else
    echo "❌ 无效选择，请输入 1 或 2"
    exit 1
fi

echo ""
echo "详细使用说明请查看 README.md 文件"