#!/usr/bin/env python3
"""
主动消息投递脚本 — 6维权重决策层 + LLM内容生成
cronjob 每5分钟触发，但不一定每次都发送：
- 权重决策层：6个维度分别打分 → 加权求和 → 超阈值才调LLM
- LLM 只负责生成内容，不再参与"发不发"的决策
- next_allowed_time 控制最早可发送时间（随机冷却）
- score_decision() 统一评分入口，--dry-run 可预览各维度得分
"""

import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib import request

STATE_FILE = os.path.expanduser("~/.hermes/proactive_chat_state.json")
TZ = timezone(timedelta(hours=8))
# 消息投递目标（必填！通过环境变量或 .env 文件配置）
# 示例：PROACTIVE_DELIVER_TARGET=qqbot:YOUR_OPENID
QQ_TARGET = os.environ.get("PROACTIVE_DELIVER_TARGET", "")

# 活跃阈值（秒）：用户如果在最近 N 秒内发过消息，就不主动打扰
ACTIVE_THRESHOLD = 600  # 10分钟

# ── 权重决策配置 ──
DECISION_WEIGHTS = {
    "cooldown": 0.25,
    "activity": 0.25,
    "context_depth": 0.10,
    "patience": 0.20,
    "time_fitness": 0.10,
    "info_signal": 0.10,
}
DECISION_THRESHOLD = 0.55
COOLDOWN_MIN = 15       # 最小冷却分钟
COOLDOWN_EXTRA_MAX = 30 # 额外随机冷却分钟上限

# [DEPRECATED] keep for compatibility — replaced by DECISION_WEIGHTS + score_decision()
# 概率衰减：连续 unanswered 越多，发送概率越低
def get_send_probability(unanswered: int) -> float:
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


def score_decision(state: dict, now_ts: float, now: datetime) -> dict:
    """6维权重决策：各维度打分 → 加权求和 → 与阈值比较。
    返回 {"total": float, "threshold": float, "should_send": bool, "details": {...}}

    维度说明：
      cooldown (0.25)  — 距上次消息越久分越高，随机冷却目标
      activity (0.25)  — 用户不活跃越久分越高，活跃期硬阻断
      context_depth (0.10) — 会话内容丰富度：无会话0.5/内容少0.3/丰富0.6
      patience (0.20)  — 连续未回复越多分越低，防止骚扰
      time_fitness (0.10) — 时段适宜度，深夜硬阻断
      info_signal (0.10)  — 外部事件信号，当前预留扩展返回0.0
    """
    details = {}

    # ── cooldown：距离上次消息越久分数越高 ──
    cooldown_target = COOLDOWN_MIN + random.randint(0, COOLDOWN_EXTRA_MAX)
    elapsed = (now_ts - state.get("last_message_time", 0)) / 60
    cooldown_score = min(1.0, elapsed / cooldown_target)
    details["cooldown"] = cooldown_score

    # ── activity：用户不活跃越久分数越高 ──
    last_user_msg = state.get("last_user_message_time", 0)
    if not last_user_msg or (now_ts - last_user_msg) < ACTIVE_THRESHOLD:
        activity_score = 0.0
    else:
        # 超过 ACTIVE_THRESHOLD 后，再过10分钟(600秒)涨到1.0
        activity_score = min(1.0, (now_ts - last_user_msg - ACTIVE_THRESHOLD) / 600)
    details["activity"] = activity_score

    # ── context_depth：会话内容丰富度 ──
    ses_data = get_recent_context(3)
    if not ses_data:
        context_score = 0.5  # 无会话：中立
    elif len(ses_data) < 2:
        context_score = 0.3  # 内容少：不太适合插话
    else:
        # 有足够会话内容，略微偏正向
        context_score = 0.6
    details["context_depth"] = context_score

    # ── patience：连续未回复越多分数越低 ──
    unanswered = state.get("unanswered_count", 0)
    if unanswered == 0:
        patience_score = 1.0
    elif unanswered == 1:
        patience_score = 0.7
    elif unanswered == 2:
        patience_score = 0.4
    else:
        patience_score = 0.1
    details["patience"] = patience_score

    # ── time_fitness：时段适宜度 ──
    hour = now.hour
    if hour < 7 or hour >= 23:
        time_score = 0.0   # 深夜硬阻断
    elif hour < 9:
        time_score = 0.3
    elif hour < 12:
        time_score = 0.7
    elif hour < 14:
        time_score = 0.9
    elif hour < 18:
        time_score = 0.6
    elif hour < 21:
        time_score = 0.8
    else:  # 21-22
        time_score = 0.4
    details["time_fitness"] = time_score

    # ── info_signal：外部事件信号（预留扩展点） ──
    info_score = 0.0
    details["info_signal"] = info_score

    # ── 加权求和 ──
    total = 0.0
    weighted = {}
    for dim, weight in DECISION_WEIGHTS.items():
        w = weight * details[dim]
        weighted[dim] = w
        total += w

    return {
        "total": round(total, 4),
        "threshold": DECISION_THRESHOLD,
        "should_send": total >= DECISION_THRESHOLD,
        "details": details,
        "weighted": weighted,
    }


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_quiet_hours(now: datetime) -> bool:
    t = now.hour * 60 + now.minute
    return t >= 1380 or t < 450


def get_deepseek_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    env_path = os.path.expanduser("~/.hermes/.env")
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
        return ""
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


def send_message(text: str) -> bool:
    try:
        result = subprocess.run(
            ["hermes", "send", "--to", QQ_TARGET, "--quiet", text],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"send failed: {e}", file=sys.stderr)
        return False


def get_hour_name(h: int) -> str:
    if h < 6: return "凌晨"
    elif h < 9: return "早上"
    elif h < 12: return "上午"
    elif h < 14: return "中午"
    elif h < 18: return "下午"
    elif h < 21: return "傍晚"
    else: return "晚上"


def _check_recent_session_activity(now_ts: float, threshold: int) -> bool:
    """
    通过会话文件的修改时间判断用户最近是否活跃。
    作为 proactive-context 插件未加载时的备选方案。
    """
    sessions_dir = os.path.expanduser("~/.hermes/sessions/")
    try:
        for fname in os.listdir(sessions_dir):
            if not fname.endswith(".json") or fname == "sessions.json":
                continue
            fpath = os.path.join(sessions_dir, fname)
            mtime = os.path.getmtime(fpath)
            if (now_ts - mtime) < threshold:
                # 这个会话文件在阈值时间内有修改 → 用户活跃
                return True
    except (FileNotFoundError, OSError):
        pass
    return False


def get_recent_context(max_exchanges: int = 3) -> list:
    sessions_dir = os.path.expanduser("~/.hermes/sessions/")
    try:
        files = sorted(
            [f for f in os.listdir(sessions_dir) if f.endswith(".json") and f != "sessions.json"],
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


def main():
    now_ts = time.time()
    now = datetime.fromtimestamp(now_ts, TZ)
    state = load_state()

    # 基础检查
    if not state.get("enabled", True):
        return
    if is_quiet_hours(now):
        return

    # 时间检查：是否到了 next_allowed_time？
    next_allowed = state.get("next_allowed_time", 0)
    if now_ts < next_allowed:
        # 还没到允许发送的时间，静默
        return

    # 活跃检查：如果用户最近发过消息，说明在聊天中，不打扰
    last_user_msg = state.get("last_user_message_time", 0)
    active_by_state = last_user_msg and (now_ts - last_user_msg) < ACTIVE_THRESHOLD

    # 备选方案：从会话文件直接检查（当插件未加载时 last_user_msg 可能过期）
    active_by_session = False
    if not active_by_state:
        active_by_session = _check_recent_session_activity(now_ts, ACTIVE_THRESHOLD)

    if active_by_state or active_by_session:
        # 用户在聊天，跳过。但把 next_allowed_time 推后不久再试
        state["next_allowed_time"] = now_ts + max(60, (ACTIVE_THRESHOLD - (now_ts - last_user_msg)))
        if active_by_session and last_user_msg:
            # 同步更新状态文件中的时间戳（给插件补坑）
            state["last_user_message_time"] = now_ts
        save_state(state)
        return

    # 用户回复检测：如果用户在上次主动消息后发过消息，重置 unanswered_count
    last_user_msg = state.get("last_user_message_time", 0)
    last_sent = state.get("last_message_time", 0)
    if last_user_msg and last_sent and last_user_msg > last_sent:
        if state.get("unanswered_count", 0) > 0:
            state["unanswered_count"] = 0
            # 重置后也把 next_allowed_time 推近，让下次更快触发
            state["next_allowed_time"] = now_ts + random.randint(5, 15) * 60
            save_state(state)
            print("用户有回复，重置 unanswered_count=0", file=sys.stderr)
            return  # 让下一轮 tick 重新评估

    # ── 权重决策 ──
    unanswered = state.get("unanswered_count", 0)
    score_result = score_decision(state, now_ts, now)
    if not score_result["should_send"]:
        # 不发，更新下次可发时间
        delay = random.randint(5, 30)
        state["next_allowed_time"] = now_ts + delay * 60
        save_state(state)
        return

    # 到这一步，决定尝试发送，让 LLM 决定发不发、发什么
    silence_minutes = int((now_ts - state.get("last_message_time", 0)) / 60)
    last_active_ts = state.get("last_active_timestamp", 0)
    recent_context = get_recent_context(3)

    system_prompt = (
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
        "- 最近聊过话题 → 可以接话聊，也可以开新话题，别硬续\n"
        "- 不要提'沉默''时间'等字眼\n"
        "- **根据当前时间和最近对话决定语气和内容**\n\n"
        '回复JSON：{"action": "send"|"silent", "message": "..."}'
    )

    user_prompt = (
        f"时间：{get_hour_name(now.hour)}{now.hour}:{now.minute:02d}\n"
        f"用户沉默：{silence_minutes} 分钟\n"
        f"连续未回消息：{unanswered} 次\n"
    )
    if recent_context:
        user_prompt += "\n最近对话：\n" + "\n".join(recent_context) + "\n"
    user_prompt += "\n请判断。"

    result_text = call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])
    if not result_text:
        return

    # 解析 JSON
    result_text = result_text.strip().strip("```json").strip("```").strip()
    try:
        llm_decision = json.loads(result_text)
    except json.JSONDecodeError:
        import re
        m = re.search(r'\{[^}]+\}', result_text)
        if m:
            try:
                llm_decision = json.loads(m.group())
            except json.JSONDecodeError:
                return
        else:
            return

    if not isinstance(llm_decision, dict):
        return

    action = llm_decision.get("action", "silent")
    message = llm_decision.get("message", "")

    if action != "send" or not message:
        # LLM 选择不发，延迟下次触发
        delay = random.randint(5, 30)
        state["next_allowed_time"] = now_ts + delay * 60
        save_state(state)
        print(f"LLM silent, next in {delay}min", file=sys.stderr)
        return

    # LLM 决定发送
    if not send_message(message):
        print("Send failed", file=sys.stderr)
        return

    print(f"Sent: {message}", file=sys.stderr)

    # 更新状态：设下次随机间隔（5~90分钟），累加 unanswered
    # 保留脚本不覆盖的字段（如 last_user_message_time 由插件维护）
    _preserve = {k: state.get(k) for k in ("last_user_message_time",) if state.get(k)}
    delay = random.randint(5, 90)
    state["last_active_message"] = message
    state["last_active_timestamp"] = now_ts
    state["last_message_time"] = now_ts
    state["next_allowed_time"] = now_ts + delay * 60
    state["unanswered_count"] = unanswered + 1
    state.update({k: v for k, v in _preserve.items() if v})  # 恢复被覆盖的字段
    save_state(state)
    print(f"Next allowed in {delay}min, unanswered={unanswered+1}", file=sys.stderr)


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        now_ts = time.time()
        now = datetime.fromtimestamp(now_ts, TZ)
        state = load_state()
        score_result = score_decision(state, now_ts, now)
        label = "SEND" if score_result["should_send"] else "SKIP"
        print(f"[DECISION] total={score_result['total']:.4f} threshold={score_result['threshold']:.2f} => {label}")
        weight_order = ["cooldown", "activity", "context_depth", "patience", "time_fitness", "info_signal"]
        name_map = {
            "cooldown": "cooldown",
            "activity": "activity",
            "context_depth": "context",
            "patience": "patience",
            "time_fitness": "time",
            "info_signal": "info",
        }
        for dim in weight_order:
            w = DECISION_WEIGHTS[dim]
            score = score_result["details"][dim]
            weighted = score_result["weighted"][dim]
            short = name_map[dim]
            print(f"  {short:>12s}:  {w:.2f} * {score:.2f}  = {weighted:.4f}")
    else:
        main()
