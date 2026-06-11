---
name: hermes-proactive-chat
description: "让AI助理学会主动找用户聊天。概率衰减 + LLM自主决策，不问候不废话，直接抛观点/吐槽/分享。"
type: tools
domain: messaging
requires:
  - hermes-cli
  - deepseek-api-key
setup:
  - check: "which hermes > /dev/null 2>&1 || { echo '❌ Hermes CLI not found'; exit 1; }"
  - check: "python3 -c 'import json, os, random, subprocess, sys, time, urllib' 2>/dev/null || { echo '❌ Python stdlib missing'; exit 1; }"
  - env: "DEEPSEEK_API_KEY"
    prompt: "输入你的 DeepSeek API Key（https://platform.deepseek.com/ 获取）"
  - env: "PROACTIVE_DELIVER_TARGET"
    prompt: "输入消息投递目标（如 qqbot:YOUR_OPENID 或 telegram:-1001234567890）"
  - copy: "proactive_send.py -> ~/.hermes/scripts/proactive_send.py"
  - cron: "*/5 * * * *"
    script: "cd ~/.hermes/scripts && python3 proactive_send.py >> ~/.hermes/logs/proactive_chat.log 2>&1"
    name: "proactive-chat"
  - message: "✅ 安装完成！状态文件: ~/.hermes/proactive_chat_state.json"
---

# Hermes Proactive Chat

AI 主动对话插件，让 Hermes Agent 学会主动找用户聊天。

## 核心机制

```
每5分钟 → 是否是安静时段？ → 是否在冷却期？ → 用户是否活跃？
         → 概率衰减(0→60%, ≥4→3%) → LLM决定发不发
```

## 自定义人格

安装后编辑 `~/.hermes/scripts/proactive_send.py`，修改 `SYSTEM_PROMPT` 变量即可。

## 手动管理

```bash
# 查看状态
cat ~/.hermes/proactive_chat_state.json

# 暂停
hermes cron pause proactive-chat

# 恢复
hermes cron resume proactive-chat

# 卸载
hermes cron remove proactive-chat
rm ~/.hermes/scripts/proactive_send.py
```
