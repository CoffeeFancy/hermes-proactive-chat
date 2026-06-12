#!/usr/bin/env python3
"""
主动消息投递脚本 — 7维权重决策层 + LLM内容生成（v3.6 异步节奏引擎）
cronjob 每5分钟触发，但不一定每次都发送：
- 权重决策层：7个维度分别打分 → 加权求和 → 超阈值才调LLM
- LLM 只负责生成内容，不再参与"发不发"的决策
- next_allowed_time 控制最早可发送时间（自适应冷却）
- score_decision() 统一评分入口，--dry-run 可预览各维度得分

v3.6 新增：
- topic_vitality 维度（LLM判断话题延续价值，权重0.10）
- rhythm_mismatch 维度（发送节奏与回复节奏匹配度，权重0.10）
- 自适应冷却（基于用户回复间隔P50/P75动态调整）
- 移除 context_depth 维度（被 topic_vitality 替代）
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
QQ_TARGET = "qqbot:2A9183321EB1B3C0FC26B0FDE3B3A9DC"

# 活跃阈值（秒）：用户如果在最近 N 秒内发过消息，就不主动打扰
ACTIVE_THRESHOLD = 600  # 10分钟

# ── 权重决策配置 ──
DECISION_WEIGHTS = {
    "cooldown": 0.25,
    "activity": 0.15,
    "patience": 0.20,
    "time_fitness": 0.10,
    "info_signal": 0.10,
    "topic_vitality": 0.10,
    "rhythm_mismatch": 0.10,
}
DECISION_THRESHOLD = 0.55  # 保持不变
COOLDOWN_MIN = 15       # 最小冷却分钟
COOLDOWN_EXTRA_MAX = 30 # 额外随机冷却分钟上限


# ── v3.6 新增：rhythm_mismatch 评分 ──
def calc_rhythm_mismatch(state):
    """计算发送节奏与用户回复节奏的匹配度。
    返回值 0.0~1.0：
      - 0.1: 发得太勤（发送间隔远小于回复间隔）
      - 0.4: 略偏频繁
      - 0.8: 节奏匹配
      - 0.6: 发得太少
      - 0.5: 数据不足，中性
    """
    send_history = state.get("send_history", [])
    reply_intervals = state.get("reply_interval_history", [])

    if len(send_history) < 3 or len(reply_intervals) < 3:
        return 0.5  # 数据不足，中性

    # 计算近5条发送消息的平均间隔（分钟）
    recent_sends = send_history[-5:]
    send_gaps = [recent_sends[i] - recent_sends[i-1] for i in range(1, len(recent_sends))]
    avg_send_gap = sum(send_gaps) / len(send_gaps) / 60  # 转为分钟

    # 计算回复平均间隔
    avg_reply_gap = sum(reply_intervals[-5:]) / len(reply_intervals[-5:])

    # 如果发送间隔 << 回复间隔，说明发太频繁了
    ratio = avg_send_gap / max(avg_reply_gap, 1)
    if ratio < 0.3:
        return 0.1  # 发得太勤
    elif ratio < 0.7:
        return 0.4
    elif ratio < 1.5:
        return 0.8  # 节奏匹配
    else:
        return 0.6  # 发得太少


# ── v3.6 新增：自适应冷却 ──
def calc_adaptive_cooldown(state):
    """基于用户历史回复间隔的自适应冷却时间。
    数据不足时回退到默认随机冷却。
    """
    reply_intervals = state.get("reply_interval_history", [])
    if len(reply_intervals) < 5:
        # 数据不足，用默认随机冷却
        return COOLDOWN_MIN + random.randint(0, COOLDOWN_EXTRA_MAX)

    # 取 P50 和 P75
    sorted_intervals = sorted(reply_intervals)
    p50 = sorted_intervals[len(sorted_intervals) // 2]
    p75 = sorted_intervals[len(sorted_intervals) * 3 // 4]

    # 冷却下限 = max(P50 * 0.8, 15分钟)
    min_cooldown = max(int(p50 * 0.8), COOLDOWN_MIN)
    # 冷却上限 = max(P75, 30分钟)
    max_cooldown = max(int(p75), COOLDOWN_MIN + COOLDOWN_EXTRA_MAX)

    return random.randint(min_cooldown, min(max_cooldown, max_cooldown))


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


def score_decision(state: dict, now_ts: float, now: datetime, topic_vitality: float = 0.5) -> dict:
    """7维权重决策：各维度打分 → 加权求和 → 与阈值比较。
    返回 {"total": float, "threshold": float, "should_send": bool, "details": {...}}

    维度说明：
      cooldown (0.25)    — 距上次消息越久分越高，自适应冷却目标
      activity (0.15)    — 用户不活跃越久分越高，活跃期硬阻断
      patience (0.20)    — 连续未回复越多分越低，防止骚扰
      time_fitness (0.10) — 时段适宜度，深夜硬阻断
      info_signal (0.10)  — 外部事件信号，当前预留扩展返回0.0
      topic_vitality (0.10) — LLM判断的话题延续价值
      rhythm_mismatch (0.10) — 发送节奏与回复节奏匹配度
    """
    details = {}

    # ── cooldown：距离上次消息越久分数越高（v3.6: 使用自适应冷却目标） ──
    cooldown_target = calc_adaptive_cooldown(state)
    elapsed = (now_ts - state.get("last_message_time", 0)) / 60
    cooldown_score = min(1.0, elapsed / cooldown_target) if cooldown_target > 0 else 1.0
    details["cooldown"] = cooldown_score

    # ── activity：用户不活跃越久分数越高 ──
    last_user_msg = state.get("last_user_message_time", 0)
    if not last_user_msg or (now_ts - last_user_msg) < ACTIVE_THRESHOLD:
        activity_score = 0.0
    else:
        # 超过 ACTIVE_THRESHOLD 后，再过10分钟(600秒)涨到1.0
        activity_score = min(1.0, (now_ts - last_user_msg - ACTIVE_THRESHOLD) / 600)
    details["activity"] = activity_score

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

    # ── topic_vitality：LLM判断的话题延续价值（参数传入，默认0.5） ──
    details["topic_vitality"] = topic_vitality

    # ── rhythm_mismatch：发送节奏与回复节奏匹配度 ──
    rhythm_score = calc_rhythm_mismatch(state)
    details["rhythm_mismatch"] = rhythm_score

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


def call_llm(messages: list, temperature: float = 0.8, max_tokens: int = 150) -> str:
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


# ── 时间感知 ──
WEEKDAY_NAMES = ["星期一","星期二","星期三","星期四","星期五","星期六","星期日"]

def get_time_context(now: datetime) -> str:
    """增强时间感知：返回详细的时间上下文，含时段氛围描述"""
    weekday = WEEKDAY_NAMES[now.weekday()]
    h = now.hour
    if h < 5:     period, vibe = "深夜", "夜深人静，适合安静简短的内容。"
    elif h < 7:   period, vibe = "凌晨", "天快亮了，简短说一声。"
    elif h < 9:   period, vibe = "早晨", "新的一天刚开始，适合早安或聊聊今天的计划。"
    elif h < 11:  period, vibe = "上午", "上午工作时间。A股正在交易（9:30-11:30）。"
    elif h < 12:  period, vibe = "午前", "快中午了，临近早盘收盘。"
    elif h < 13:  period, vibe = "中午", "午饭时间，适合轻松话题。"
    elif h < 14:  period, vibe = "午后", "午后刚开盘，容易犯困。"
    elif h < 15:  period, vibe = "下午", "下午交易时段，收盘前适合聊聊持股。"
    elif h < 17:  period, vibe = "下午", "下午后半段，适合聊聊今日进展或收盘总结。"
    elif h < 19:  period, vibe = "傍晚", "傍晚下班时间，适合聊聊今天的收获。"
    elif h < 21:  period, vibe = "晚上", "晚上自由时间，适合聊聊白天的事。"
    elif h < 23:  period, vibe = "夜间", "快休息了，适合简短收尾。"
    else:         period, vibe = "深夜", "深夜了，适合极简短的内容。"
    return (
        f"**时间上下文**\n"
        f"现在是 {now.strftime('%Y年%m月%d日')} {weekday}，{period}（{now.hour}:{now.minute:02d}）。\n"
        f"氛围：{vibe}\n"
    )


# ── 上下文来源三模式 ──

def _check_recent_session_activity(now_ts: float, threshold: int) -> bool:
    """通过会话文件的修改时间判断用户最近是否活跃（备选方案）"""
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



def get_qqbot_session_path() -> str:
    """找最近的 qqbot 会话文件"""
    sessions_dir = os.path.expanduser("~/.hermes/sessions/")
    try:
        files = sorted(
            [f for f in os.listdir(sessions_dir) if f.endswith(".json") and f != "sessions.json"],
            key=lambda f: os.path.getmtime(os.path.join(sessions_dir, f)),
            reverse=True,
        )
        for fname in files:
            # 优先找 qqbot 相关的会话
            if "qqbot" in fname or "2A918" in fname:
                return os.path.join(sessions_dir, fname)
        if files:
            return os.path.join(sessions_dir, files[0])
    except (FileNotFoundError, OSError):
        pass
    return ""


def extract_messages_from_session(filepath: str, max_msgs: int = 10) -> list:
    """从会话文件中提取消息列表"""
    try:
        with open(filepath) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    msgs = data.get("messages", data.get("history", data.get("conversation", [])))
    if not msgs or not isinstance(msgs, list):
        return []
    return msgs[-max_msgs:]


def get_context_conversation_history(max_exchanges: int = 3) -> list:
    """模式1: conversation_history — 当前对话历史"""
    fpath = get_qqbot_session_path()
    if not fpath:
        return []
    msgs = extract_messages_from_session(fpath, max_exchanges * 4)
    exchanges = []
    for msg in msgs:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))
        if not content or content.strip() == "":
            continue
        if role in ("user", "human", "assistant", "ai"):
            label = "用户" if role in ("user", "human") else "我"
            exchanges.append(f"{label}: {content.strip()[:200]}")
        if len(exchanges) >= max_exchanges * 2:
            break
    return exchanges


def get_context_platform_history(max_messages: int = 10) -> list:
    """模式2: platform_message_history — 平台最近消息流水"""
    sessions_dir = os.path.expanduser("~/.hermes/sessions/")
    try:
        files = sorted(
            [f for f in os.listdir(sessions_dir) if f.endswith(".json") and f != "sessions.json"],
            key=lambda f: os.path.getmtime(os.path.join(sessions_dir, f)),
            reverse=True,
        )
    except (FileNotFoundError, OSError):
        return []
    all_msgs = []
    for fname in files[:5]:
        fpath = os.path.join(sessions_dir, fname)
        msgs = extract_messages_from_session(fpath, 6)
        for msg in msgs:
            role = msg.get("role", "")
            content = str(msg.get("content", ""))
            if not content or content.strip() == "":
                continue
            if role in ("user", "human", "assistant", "ai"):
                label = "用户" if role in ("user", "human") else "我"
                all_msgs.append(f"{label}: {content.strip()[:200]}")
            if len(all_msgs) >= max_messages:
                break
        if len(all_msgs) >= max_messages:
            break
    return all_msgs


def get_context(selected_mode: str = "conversation_history", max_items: int = 3) -> dict:
    """根据选择模式获取上下文
    
    返回: {"mode": str, "context": list, "description": str}
    """
    if selected_mode == "platform_message_history":
        ctx = get_context_platform_history(max_items * 3)
        return {
            "mode": "platform_message_history",
            "context": ctx,
            "description": "平台最近消息流水（按时间排序，新到旧）",
        }
    elif selected_mode == "hybrid":
        conv = get_context_conversation_history(max_items)
        plat = get_context_platform_history(max_items * 2)
        # 合并，去重
        seen = set()
        merged = []
        for item in conv + plat:
            if item not in seen:
                seen.add(item)
                merged.append(item)
        return {
            "mode": "hybrid",
            "context": merged[:max_items * 3],
            "description": "当前对话历史 + 平台最近消息流水（合并去重）",
        }
    else:  # conversation_history (default)
        ctx = get_context_conversation_history(max_items)
        return {
            "mode": "conversation_history",
            "context": ctx,
            "description": "当前对话历史（最近几次交流）",
        }


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
        # v3.6: 记录回复间隔
        reply_interval = int((last_user_msg - last_sent) / 60)  # 分钟
        reply_intervals = state.get("reply_interval_history", [])
        reply_intervals.append(reply_interval)
        # 保留最近20条
        state["reply_interval_history"] = reply_intervals[-20:]

        if state.get("unanswered_count", 0) > 0:
            state["unanswered_count"] = 0
            # 重置后也把 next_allowed_time 推近，让下次更快触发
            state["next_allowed_time"] = now_ts + random.randint(5, 15) * 60
            save_state(state)
            print(f"用户有回复，重置 unanswered_count=0，回复间隔={reply_interval}min", file=sys.stderr)
            return  # 让下一轮 tick 重新评估

    # ── 权重决策（topic_vitality 先用默认值 0.5） ──
    unanswered = state.get("unanswered_count", 0)
    score_result = score_decision(state, now_ts, now, topic_vitality=0.5)
    if not score_result["should_send"]:
        # 不发，更新下次可发时间（使用自适应冷却）
        delay = calc_adaptive_cooldown(state)
        state["next_allowed_time"] = now_ts + delay * 60
        save_state(state)
        return

    # 到这一步，决定尝试发送，让 LLM 决定发不发、发什么
    silence_minutes = int((now_ts - state.get("last_message_time", 0)) / 60)
    last_active_ts = state.get("last_active_timestamp", 0)
    
    # 获取时间上下文
    time_context = get_time_context(now)
    
    # 获取上下文来源（从状态文件中读取当前模式，默认 conversation_history）
    context_source = state.get("context_source", "conversation_history")
    ctx_data = get_context(context_source, 3)

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
        "- 接到最近聊的话题就自然接话，没啥好接的就果断开新话题，别硬续\n"
        "- 不要提'沉默''时间'等字眼\n"
        "- **时间感知**：结合下面的时间上下文决定说什么——时间不同，聊的内容完全不同\n"
        "- **话题冷卻**：同一个话题主动聊过一次后，同一天内不要再聊第二次\n"
        "- **不聊股票**：不要主动聊A股行情、个股分析、股票推荐。老大想聊会自己提。\n\n"
        "回复JSON：{\"action\": \"send\"|\"silent\", \"message\": \"...\", \"topic_vitality\": 0.8}\n"
        "topic_vitality 取值 0.0~1.0，反映当前对话历史中最近话题还有没有延续价值：\n"
        "  - 有明确话题延伸空间 → 0.6~1.0\n"
        "  - 话题已聊尽或干巴巴 → 0.0~0.4\n"
        "  - 无上下文/刚睡醒首次触发 → 0.5"
    )

    user_prompt = (
        f"{time_context}"
        f"用户沉默：{silence_minutes} 分钟\n"
        f"连续未回消息：{unanswered} 次\n"
    )
    if ctx_data["context"]:
        context_text = "\n".join(ctx_data["context"])
        user_prompt += (
            f"\n【上下文来源：{ctx_data['description']}】\n"
            f"{context_text}\n"
        )
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
    topic_vitality_score = llm_decision.get("topic_vitality", 0.5)

    # v3.6: 用 LLM 返回的 topic_vitality 重新评估总得分
    score_result = score_decision(state, now_ts, now, topic_vitality=topic_vitality_score)

    if action != "send" or not message:
        # LLM 选择不发，延迟下次触发（自适应冷却）
        delay = calc_adaptive_cooldown(state)
        state["next_allowed_time"] = now_ts + delay * 60
        save_state(state)
        print(f"LLM silent, next in {delay}min", file=sys.stderr)
        return

    # v3.6: 如果 topic_vitality 太低导致总分不达标，也跳过
    if not score_result["should_send"]:
        delay = calc_adaptive_cooldown(state)
        state["next_allowed_time"] = now_ts + delay * 60
        save_state(state)
        print(f"LLM says send but topic_vitality={topic_vitality_score:.2f} too low, total={score_result['total']:.4f}, skip", file=sys.stderr)
        return

    # LLM 决定发送
    if not send_message(message):
        print("Send failed", file=sys.stderr)
        return

    print(f"Sent: {message}", file=sys.stderr)

    # ── 更新状态 ──
    # 保留脚本不覆盖的字段（last_user_message_time 由插件维护）
    _preserve = {k: state.get(k) for k in ("last_user_message_time",) if state.get(k)}

    # v3.6: 自适应冷却
    delay = calc_adaptive_cooldown(state)
    state["last_active_message"] = message
    state["last_active_timestamp"] = now_ts
    state["last_message_time"] = now_ts
    state["next_allowed_time"] = now_ts + delay * 60
    state["unanswered_count"] = unanswered + 1

    # v3.6: 记录发送历史
    send_history = state.get("send_history", [])
    send_history.append(now_ts)
    state["send_history"] = send_history[-20:]  # 保留最近20条

    state.update({k: v for k, v in _preserve.items() if v})  # 恢复被覆盖的字段
    save_state(state)
    print(f"Next allowed in {delay}min, unanswered={unanswered+1}, topic_vitality={topic_vitality_score:.2f}", file=sys.stderr)


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        now_ts = time.time()
        now = datetime.fromtimestamp(now_ts, TZ)
        state = load_state()
        # dry-run 使用默认 topic_vitality=0.5
        score_result = score_decision(state, now_ts, now, topic_vitality=0.5)
        label = "SEND" if score_result["should_send"] else "SKIP"
        print(f"[DECISION] total={score_result['total']:.4f} threshold={score_result['threshold']:.2f} => {label}")
        weight_order = ["cooldown", "activity", "patience", "time_fitness", "info_signal", "topic_vitality", "rhythm_mismatch"]
        name_map = {
            "cooldown": "cooldown",
            "activity": "activity",
            "patience": "patience",
            "time_fitness": "time",
            "info_signal": "info",
            "topic_vitality": "vitality",
            "rhythm_mismatch": "rhythm",
        }
        for dim in weight_order:
            w = DECISION_WEIGHTS[dim]
            score = score_result["details"][dim]
            weighted = score_result["weighted"][dim]
            short = name_map[dim]
            print(f"  {short:>12s}:  {w:.2f} * {score:.2f}  = {weighted:.4f}")
    else:
        main()
