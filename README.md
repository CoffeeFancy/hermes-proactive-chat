# Hermes Proactive Chat 🤖

让 AI 助理学会**主动找用户聊天**，而不是永远被动等用户开口。

An AI assistant that learns to **initiate conversations**, instead of always waiting for the user to speak first.

---

## 这解决了什么问题？/ The Problem

大部分 AI 助理/聊天机器人都是 **「你问我答」** 模式——用户不说话就死寂。

Most AI assistants are **Q&A-only** — silent until the user speaks.

真正像人的对话，AI 也应该能：
- 看到用户在忙某个事，主动吐槽一句
- 发现一个有意思的东西，分享给用户
- 之前聊到一半的话题，过一会儿补一句后续想法

A human-like conversation should go both ways:
- Notice the user is busy with something → throw in a casual remark
- Find something interesting → share it
- Remember a half-finished topic → follow up later

---

## 怎么工作？/ How It Works

```
每 5 分钟触发一次 / Tick every 5 minutes
  │
  ├─ 安静时段？         → 跳过（跳过（默认 23:00~07:30）
  ├─ Quiet hours?       → Skip
  ├─ 还在冷却期？        → 跳过（发完后随机冷却 5~90 分钟）
  ├─ Still cooling down? → Skip (random 5~90 min cooldown)
  ├─ 用户最近有消息？     → 跳过（不打扰正在聊天的用户）
  ├─ User recently active? → Skip (don't interrupt)
  │
  ├─ 概率衰减 / Probability decay
  │   连续未回复 0 次/unanswered=0 → 60%
  │   连续未回复 1 次/unanswered=1 → 30%
  │   连续未回复 2 次/unanswered=2 → 15%
  │   连续未回复 ≥4 次/unanswered≥4 → 3%
  │
  └─ LLM 决定 / LLM decides
       DeepSeek 看当前上下文 + 时间 → 决定发不发、发什么
       DeepSeek reads context + time → decides whether to send & what to say
       原则：不问候、不废话、直接抛观点
       Rule: no greetings, no fluff, just opinions
```

### 核心机制：概率衰减 / Core Mechanism

> **如果你连续不回，AI 会越来越不想打扰你。**
> 一旦你回了一句，立刻重置到最活跃状态。
>
> **The more you ignore it, the quieter it gets.**
> One reply from you resets it to full eagerness.

AI 不是定时炸弹，它懂得看眼色。

It's not a nagging timer — it reads the room.

---

## 快速开始 / Quick Start

### 如果你有 Hermes Agent / If You Have Hermes

**一条命令安装（推荐）：**

```bash
hermes skill install CoffeeFancy/hermes-proactive-chat
```

安装过程中会提示输入 DeepSeek API Key 和消息投递目标，自动配置 cron 定时任务。

### 手动安装 / Manual Setup

**依赖 / Dependencies：**

- **Python 3.8+**（只用标准库，零 pip 依赖 / pure stdlib, zero pip deps）
- **Hermes Agent**（消息投递 / message delivery, [安装 / Install](https://github.com/NousResearch/hermes)）
- **DeepSeek API Key**（[注册 / Sign up](https://platform.deepseek.com/)）

### 安装 / Setup

```bash
git clone https://github.com/CoffeeFancy/hermes-proactive-chat.git
cd hermes-proactive-chat

# 配置 API Key / Configure API Key
cp .env.example .env
# 编辑 .env，填入你的 DEEPSEEK_API_KEY
# Edit .env, fill in your DEEPSEEK_API_KEY
```

### 运行 / Run

```bash
# 手动测试 / Manual test
python3 proactive_send.py

# 用 cron 每 5 分钟跑一次 / Run via cron every 5 minutes
crontab -e
# 添加 / Add:
*/5 * * * * cd /path/to/hermes-proactive-chat && python3 proactive_send.py >> run.log 2>&1
```

### 配置项 / Configuration

| 环境变量 / Env Var | 默认值 / Default | 说明 / Description |
|---|---|---|
| `DEEPSEEK_API_KEY` | (必填/Required) | DeepSeek API Key |
| `PROACTIVE_DELIVER_TARGET` | (必填/Required) | Hermes send 目标 / Delivery target |
| `PROACTIVE_ACTIVE_THRESHOLD` | `600` | 用户活跃阈值（秒）/ Active threshold (s) |
| `PROACTIVE_QUIET_START` | `1380` | 安静时段开始 / Quiet hours start |
| `PROACTIVE_QUIET_END` | `450` | 安静时段结束 / Quiet hours end |

---

## 状态文件 / State File

`~/.hermes/proactive_chat_state.json` 或 `PROACTIVE_STATE_FILE`：

```json
{
  "enabled": true,
  "last_message_time": 1749600000.0,
  "next_allowed_time": 1749603600.0,
  "unanswered_count": 0,
  "last_user_message_time": 1749600000.0,
  "last_active_message": "华天这走势不太妙",
  "last_active_timestamp": 1749600000.0
}
```

---

## 自定义人格 / Custom Personality

AI 的主动行为由一段**提示词**控制。打开 `proactive_send.py`，找到 `SYSTEM_PROMPT` 替换即可。

The AI's proactive behavior is controlled by a `SYSTEM_PROMPT`. Open `proactive_send.py` and replace it:

```python
SYSTEM_PROMPT = (
    "你是小墨，老大的AI助理总监。\n"
    ...
)
```

换成你的：/ Try yours:

```python
SYSTEM_PROMPT = (
    "You are a witty but slightly grumpy old friend...\n"
)
```

---

## 致谢 / Credits

本项目源自 [**Open-LLM-VTuber**](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) 项目，摘取了其中的主动对话调度思路和 LLM 自主决策模式，并参考了 [**AllenReder**](https://github.com/AllenReder) 的 [**hermes-active-message**](https://github.com/AllenReder/hermes-active-message) 项目改造而来。

This project draws the proactive chat scheduling and LLM decision-making pattern from [**Open-LLM-VTuber**](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber), and is adapted from [**AllenReder**](https://github.com/AllenReder)'s [**hermes-active-message**](https://github.com/AllenReder/hermes-active-message).

此外还使用了/Also uses:
- [Hermes Agent](https://github.com/NousResearch/hermes) — 消息投递与调度 / Message delivery & scheduling
- QQ Bot / OpenClaw — 消息收发底层协议 / Messaging protocol layer

感谢以上开源社区的贡献。/ Thanks to all open-source contributors.

---

## License

MIT
