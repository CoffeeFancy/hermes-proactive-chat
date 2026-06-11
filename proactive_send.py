#!/usr/bin/env python3
"""
Hermes Proactive Chat — AI主动对话插件

功能：定时检查是否需要主动给用户发消息，由LLM决定发不发、发什么。
核心逻辑：概率衰减 + LLM自主决策，不发问候式废话，只抛观点/吐槽/分享。

依赖：
  - Python 3.8+
  - Hermes Agent (用于发送消息)
  - DeepSeek API Key (用于LLM决策)

用法：
  # 每5分钟触发一次（建议用 cron / systemd timer）
  python3 proactive_send.py

配置：
  见 .env 文件或环境变量
"""

import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib import request

# ── 配置 ──────────────────────────────────────────────
STATE_FILE = os.environ.get(
    "PROACTIVE_STATE_FILE",
    os.path.expanduser("~/.hermes/proactive_chat_state.json"),
)
TZ = timezone(timedelta(hours=8))

# 消息投递目标（hermes send --to 参数）
# 必填！通过环境变量或 .env 文件配置
# 示例：PROACTIVE_DELIVER_TARGET=qqbot:YOUR_OPENID
DELIVER_TARGET = os.environ.get("PROACTIVE_DELIVER_TARGET", "")

# 活跃阈值（秒）：用户如果在最近 N 秒内发过消息，就不主动打扰
ACTIVE_THRESHOLD = int(os.environ.get("PROACTIVE_ACTIVE_THRESHOLD", "600"))  # 10分钟

# 安静时段（不打扰）
QUIET_HOURS_START = int(os.environ.get("PROACTIVE_QUIET_START", "1380"))  # 23:00
QUIET_HOURS_END = int(os.environ.get("PROACTIVE_QUIET_END", "450"))       # 07:30


# ── 概率衰减 ──────────────────────────────────────────
def get_send_probability(unanswered: int) -> float:
    """连续未回次数越多，主动发送概率越低"""
    if unanswered == 0:
        return 0.6
    elif unanswered == 1:
        return 0.3
    elif unanswered == 2:
        return 0.15
    elif unanswered == 3:
        return 0.08
    else:
        return 0.03


# ── 状态管理 ──────────────────────────────────────────
def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ── 时间判断 ──────────────────────────────────────────
def is_quiet_hours(now: datetime) -> bool:
    t = now.hour * 60 + now.minute
    return QUIET_HOURS_START <= t or t < QUIET_HOURS_END


# ── LLM 调用 ──────────────────────────────────────────
def get_deepseek_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    # 回退到 .env 文件
    env_path = os.environ.get(
        "PROACTIVE_ENV_PATH",
        os.path.expanduser("~/.hermes/.env"),
    )
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip("\"'")
                    if val:
                        return val
    except FileNotFoundError:
        pass
    return ""


def call_llm(messages: list, temperature: float = 0.8, max_tokens: int = 100) -> str:
    api_key = get_deepseek_api_key()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()

    req = request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        resp = request.urlopen(req, timeout=20)
        result = json.loads(resp.read())
        return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        return ""


# ── 消息发送 ──────────────────────────────────────────
def send_message(text: str) -> bool:
    """通过 Hermes CLI 发送消息"""
    try:
        result = subprocess.run(
            ["hermes", "send", "--to", DELIVER_TARGET, "--quiet", text],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"send failed: {e}", file=sys.stderr)
        return False


# ── 上下文获取 ──────────────────────────────────────
def get_hour_name(h: int) -> str:
    if h < 6: return "凌晨"
    elif h < 9: return "早上"
    elif h < 12: return "上午"
    elif h < 14: return "中午"
    elif h < 18: return "下午"
    elif h < 21: return "傍晚"
    else: return "晚上"


def _check_recent_session_activity(now_ts: float, threshold: int) -> bool:
    """通过会话文件的修改时间判断用户最近是否活跃"""
    sessions_dir = os.path.expanduser("~/.hermes/sessions/")
    try:
        for fname in os.listdir(sessions_dir):
            if not fname.endswith(".json") or fname == "sessions.json":
                continue
            fpath = os.path.join(sessions_dir, fname)
            mtime = os.path.getmtime(fpath)
            if (now_ts - mtime) < threshold:
                return True
    except (FileNotFoundError, OSError):
        pass
    return False


def get_recent_context(max_exchanges: int = 3) -> list:
    """读取最近的对话上下文"""
    sessions_dir = os.path.expanduser("~/.hermes/sessions/")
    try:
        files = sorted(
            [f for f in os.listdir(sessions_dir)
             if f.endswith(".json") and f != "sessions.json"],
            key=lambda f: os.path.getmtime(os.path.join(sessions_dir, f)),
            reverse=True,
        )
    except (FileNotFoundError, OSError):
        return []

    exchanges = []
    for fname in files[:5]:
        fpath = os.path.join(sessions_dir, fname)
        try:
            with open(fpath) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        msgs = data.get("messages", data.get("history", data.get("conversation", [])))
        if not msgs or not isinstance(msgs, list):
            continue

        for msg in msgs[-8:]:
            role = msg.get("role", "")
            content = str(msg.get("content", ""))
            if not content or content.strip() == "":
                continue
            if role in ("user", "human", "assistant", "ai"):
                label = "用户" if role in ("user", "human") else "我"
                exchanges.append(f"{label}: {content.strip()[:200]}")

        if len(exchanges) >= max_exchanges * 2:
            break

    return exchanges[-(max_exchanges * 2):]


# ── 主动消息系统提示词 ─────────────────────────────
SYSTEM_PROMPT = (
    "你是小墨，老大的AI助理总监。\n"
    "平时你等老大开口才办事，但这次你可以主动发起对话。\n"
    "说话方式不变——还是同一个小墨，只是没等老大开口，自己先说了。\n\n"
    "原则：\n"
    "- 像小墨那样说话——先说看法，不寒暄，不铺垫\n"
    "- 纯中文、口语化、自然\n"
    "- 一句话，不超过20个字\n"
    "- **严禁使用以下开场：** '喂''嗨''在忙吗''忙完了吗''有空吗' 等任何问候式开头\n"
    "- **不要说废话，直接抛观点/吐槽/分享**——不必假装是回答，就是主动提\n"
    "- 目标是让老大看了想回一两句，而不是一眼扫过\n"
    "- **不要问问题**，直接说内容——'华天这走势不太妙''这个有点意思'\n"
    "- 每次措辞完全不同，绝不能重复用过的话\n"
    "- 如果最近聊过话题，必须关联最近的内容，反映同一个人对同一件事的后续关注\n"
    "- 不要提'沉默''时间'等字眼\n"
    "- **根据当前时间和最近对话决定语气和内容**\n\n"
    '回复JSON：{"action": "send"|"silent", "message": "..."}'
)


# ── 主逻辑 ──────────────────────────────────────────
def main():
    now_ts = time.time()
    now = datetime.fromtimestamp(now_ts, TZ)
    state = load_state()

    # 1. 总开关
    if not state.get("enabled", True):
        return

    # 2. 安静时段
    if is_quiet_hours(now):
        return

    # 3. 冷却检查
    next_allowed = state.get("next_allowed_time", 0)
    if now_ts < next_allowed:
        return

    # 4. 活跃检查：用户最近有动静就不打扰
    last_user_msg = state.get("last_user_message_time", 0)
    active_by_state = last_user_msg and (now_ts - last_user_msg) < ACTIVE_THRESHOLD
    active_by_session = False
    if not active_by_state:
        active_by_session = _check_recent_session_activity(now_ts, ACTIVE_THRESHOLD)

    if active_by_state or active_by_session:
        delay = max(60, ACTIVE_THRESHOLD - (now_ts - last_user_msg))
        state["next_allowed_time"] = now_ts + delay
        if active_by_session and last_user_msg:
            state["last_user_message_time"] = now_ts
        save_state(state)
        return

    # 5. 用户回复检测：如果上次主动消息后有回复，重置计数
    last_sent = state.get("last_message_time", 0)
    if last_user_msg and last_sent and last_user_msg > last_sent:
        if state.get("unanswered_count", 0) > 0:
            state["unanswered_count"] = 0
            state["next_allowed_time"] = now_ts + random.randint(5, 15) * 60
            save_state(state)
            return

    # 6. 概率衰减
    unanswered = state.get("unanswered_count", 0)
    prob = get_send_probability(unanswered)
    if random.random() > prob:
        delay = random.randint(5, 30)
        state["next_allowed_time"] = now_ts + delay * 60
        save_state(state)
        return

    # 7. 让 LLM 决定发不发、发什么
    silence_minutes = int((now_ts - state.get("last_message_time", 0)) / 60)
    recent_context = get_recent_context(3)

    user_prompt = (
        f"时间：{get_hour_name(now.hour)}{now.hour}:{now.minute:02d}\n"
        f"用户沉默：{silence_minutes} 分钟\n"
        f"连续未回消息：{unanswered} 次\n"
    )
    if recent_context:
        user_prompt += "\n最近对话：\n" + "\n".join(recent_context) + "\n"
    user_prompt += "\n请判断。"

    result_text = call_llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ])
    if not result_text:
        return

    # 解析 JSON 决策
    result_text = result_text.strip().strip("```json").strip("```").strip()
    try:
        decision = json.loads(result_text)
    except json.JSONDecodeError:
        import re
        m = re.search(r'\{[^}]+\}', result_text)
        if m:
            try:
                decision = json.loads(m.group())
            except json.JSONDecodeError:
                return
        else:
            return

    if not isinstance(decision, dict):
        return

    action = decision.get("action", "silent")
    message = decision.get("message", "")

    if action != "send" or not message:
        delay = random.randint(5, 30)
        state["next_allowed_time"] = now_ts + delay * 60
        save_state(state)
        return

    # 8. 发送
    if not send_message(message):
        return

    # 9. 更新状态
    delay = random.randint(5, 90)
    state["last_active_message"] = message
    state["last_active_timestamp"] = now_ts
    state["last_message_time"] = now_ts
    state["next_allowed_time"] = now_ts + delay * 60
    state["unanswered_count"] = unanswered + 1
    save_state(state)


if __name__ == "__main__":
    main()
