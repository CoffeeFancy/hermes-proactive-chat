#!/bin/bash
set -e

# ── Hermes Proactive Chat 一键安装脚本 ──────────────
# 用法：curl -fsSL https://raw.githubusercontent.com/CoffeeFancy/hermes-proactive-chat/main/install.sh | bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Hermes Proactive Chat 安装程序 ===${NC}"

# 1. 检查依赖
echo -e "\n${YELLOW}[1/4] 检查依赖...${NC}"
if ! command -v hermes &> /dev/null; then
    echo -e "${RED}❌ 未找到 Hermes CLI${NC}"
    echo "请先安装 Hermes Agent: https://github.com/NousResearch/hermes"
    exit 1
fi
echo -e "${GREEN}✅ Hermes CLI 已安装${NC}"

if ! python3 -c "import json, os, random, subprocess, sys, time, urllib" 2>/dev/null; then
    echo -e "${RED}❌ Python 标准库缺失${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Python 3 正常${NC}"

# 2. 配置环境变量
echo -e "\n${YELLOW}[2/4] 配置环境变量...${NC}"
ENV_FILE="$HOME/.hermes/.env"
mkdir -p "$HOME/.hermes"

if [ ! -f "$ENV_FILE" ] || ! grep -q "DEEPSEEK_API_KEY" "$ENV_FILE" 2>/dev/null; then
    read -p "输入你的 DeepSeek API Key (https://platform.deepseek.com): " DEEPSEEK_KEY
    echo "DEEPSEEK_API_KEY=$DEEPSEEK_KEY" >> "$ENV_FILE"
    echo -e "${GREEN}✅ DEEPSEEK_API_KEY 已配置${NC}"
fi

if [ ! -f "$ENV_FILE" ] || ! grep -q "PROACTIVE_DELIVER_TARGET" "$ENV_FILE" 2>/dev/null; then
    read -p "输入消息投递目标 (如 qqbot:YOUR_OPENID): " DELIVER_TARGET
    echo "PROACTIVE_DELIVER_TARGET=$DELIVER_TARGET" >> "$ENV_FILE"
    echo -e "${GREEN}✅ PROACTIVE_DELIVER_TARGET 已配置${NC}"
fi

# 3. 复制脚本
echo -e "\n${YELLOW}[3/4] 部署脚本...${NC}"
SCRIPT_DIR="$HOME/.hermes/scripts"
mkdir -p "$SCRIPT_DIR"
SCRIPT_PATH="$SCRIPT_DIR/proactive_send.py"

if [ -f "$SCRIPT_PATH" ]; then
    cp "$SCRIPT_PATH" "$SCRIPT_PATH.bak"
    echo "备份旧脚本: $SCRIPT_PATH.bak"
fi

cp proactive_send.py "$SCRIPT_PATH"
chmod +x "$SCRIPT_PATH"
echo -e "${GREEN}✅ 脚本已部署到 $SCRIPT_PATH${NC}"

# 4. 配置 cron
echo -e "\n${YELLOW}[4/4] 配置定时任务...${NC}"
CRON_JOB="*/5 * * * * cd $SCRIPT_DIR && python3 proactive_send.py >> $HOME/.hermes/logs/proactive_chat.log 2>&1"

(crontab -l 2>/dev/null | grep -v "proactive_send.py"; echo "$CRON_JOB") | crontab -
echo -e "${GREEN}✅ Cron 任务已添加（每5分钟）${NC}"

# 完成
echo -e "\n${GREEN}=== 🎉 安装完成 ===${NC}"
echo -e "状态文件: ~/.hermes/proactive_chat_state.json"
echo -e "运行日志: ~/.hermes/logs/proactive_chat.log"
echo -e "\n手动测试: cd $SCRIPT_DIR && python3 proactive_send.py"
