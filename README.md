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
  ├─ 安静时段？         → 跳过（默认 23:00~07:30）
  ├─ Quiet hours?       → Skip
  ├─ 还在冷却期？        → 跳过（自适应冷却：基于回复节奏动态调整）
  ├─ Still cooling down? → Skip (adaptive cooldown based on reply rhythm)
  ├─ 用户最近有消息？     → 跳过（不打扰正在聊天的用户）
  ├─ User recently active? → Skip (don't interrupt)
  │
  └─ 7 维权重决策 / 7-Dimension Weighted Decision
       ┌─────────────────────────────────────────────┐
       │ 冷却因子  × 0.25  上次发送后过了多久         │
       │ 活跃因子  × 0.15  用户沉默了多久             │
       │ 耐心衰减  × 0.20  连续未回复了几次           │
       │ 时间适宜  × 0.10  当前时段是否适合说话       │
       │ 信息信号  × 0.10  预留扩展（新闻/股价等）   │
       │ 话题活力  × 0.10  LLM评估话题有无延续价值 ✨ │
       │ 节奏匹配  × 0.10  发送节奏vs回复节奏 ✨      │
       └───────────────┬─────────────────────────────┘
                       │
                   总分 ≥ 0.55？
                  /             \
                是               否
                │                └─ 跳过
        LLM 生成内容
        （只负责说什么，不决定发不发）
                │
             发送
```

### 核心机制：7 维权重决策 + 时间感知 + 自适应冷却 / Core Mechanism

> **多维度评分，纯数学计算决定"发不发"，LLM 只负责"发什么"。**
>
> **Multi-dimension scoring: pure math decides IF to send, LLM only decides WHAT.**

- **v3.6 异步节奏引擎 ✨**：新增两个维度——**话题活力**（LLM 评估当前话题有无延续价值）和**节奏匹配**（比较发送节奏 vs 回复节奏），替换原来的"语境深度"维度
- **v3.6 Async Rhythm Engine ✨**: Two new dimensions — **Topic Vitality** (LLM evaluates if the current topic is worth continuing) and **Rhythm Match** (compares send frequency vs reply frequency), replacing the old "context depth" dimension
- **自适应冷却**：不再用固定随机冷却，而是根据用户的回复间隔 P50/P75 动态调整冷却时间，节奏自然对齐
- **Adaptive Cooldown**: Replaces fixed random cooldown with dynamic adjustment based on the user's reply interval P50/P75, naturally matching your conversation rhythm
- **时间感知**：LLM 决策时注入星期、时段、氛围描述，让消息内容因时因地自然切换
- **Time Awareness**: Injects day-of-week, time period, and contextual atmosphere into LLM decisions, making messages naturally match the time of day
- **上下文三模式**：可选 `conversation_history`（当前对话）、`platform_message_history`（平台流水）、`hybrid`（混合），避免重复聊同一话题
- **Three Context Modes**: Choose from `conversation_history` (current session), `platform_message_history` (platform-wide feed), or `hybrid` (merged), avoiding repetitive topics
- **话题冷却**：同一天内主动聊过的话题不再聊第二次
- **Topic Cooldown**: Topics initiated once won't be revisited the same day

大部分 tick 在评分阶段就被筛掉，不调 LLM，节省 token。

Most ticks are filtered out at the scoring stage without calling the LLM at all.

### 调试 / Debug

```bash
# 查看实时决策分数
python3 proactive_send.py --dry-run

# 输出示例 / Example output
# [DECISION] total=0.64 threshold=0.55 => SEND
#   cooldown:  0.25 * 0.91  = 0.2275
#   activity:  0.15 * 0.80  = 0.1200
#   patience:  0.20 * 0.70  = 0.1400
#   time:      0.10 * 0.60  = 0.0600
#   info:      0.10 * 0.00  = 0.0000
#   vitality:  0.10 * 0.70  = 0.0700  ✨
#   rhythm:    0.10 * 0.80  = 0.0800  ✨
```

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
| `PROACTIVE_CONTEXT_SOURCE` | `conversation_history` | 上下文来源模式 / Context source mode (`conversation_history` / `platform_message_history` / `hybrid`) |
| `PROACTIVE_STATE_FILE` | `~/.hermes/proactive_chat_state.json` | 状态文件路径 / State file path |

---

## 状态文件 / State File

`~/.hermes/proactive_chat_state.json` 或 `PROACTIVE_STATE_FILE`：

```json
{
  "enabled": true,
  "last_message_time": 1749600000.0,
  "next_allowed_time": 1749603600.0,
  "unanswered_count": 0,
  "context_source": "conversation_history",
  "last_user_message_time": 1749600000.0,
  "last_active_message": "这代码写的太烂了",
  "last_active_timestamp": 1749600000.0,
  "send_history": [1749600000.0, 1749603600.0],
  "reply_interval_history": [45, 120, 30]
}
```

`send_history` 和 `reply_interval_history` 由脚本自动维护，用于自适应冷却计算。

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

**v3.6 异步节奏引擎灵感 / v3.6 Asynchronous Rhythm Engine Inspiration：**
- [**Time to Talk: LLM Agents for Asynchronous Group Communication in Mafia Games**](https://niveck.github.io/Time-to-Talk/) (arXiv 2506.05309) — 异步LLM Agent的"什么时候说"决策机制，启发了话题活力评分和节奏匹配维度 / Asynchronous LLM agent "when to speak" decision mechanism, inspired topic vitality scoring and rhythm matching
- [**Beyond Turn-taking: Introducing Text-based Overlap into Human-LLM Interactions**](https://arxiv.org/abs/2501.18103) (arXiv 2501.18103) — 打破一问一答轮次模式，启发了节奏自适应冷却设计 / Breaking the turn-taking paradigm, inspired adaptive cooldown design

**v3.5 灵感来源 / v3.5 Inspiration：**
- [**astrbot_plugin_proactive_chat**](https://github.com/DBJD-CR/astrbot_plugin_proactive_chat) by DBJD-CR — 时间感知和上下文三模式的设计参考了此 AstrBot 插件的思路

此外还使用了/Also uses：
- [Hermes Agent](https://github.com/NousResearch/hermes) — 消息投递与调度 / Message delivery & scheduling
- QQ Bot / OpenClaw — 消息收发底层协议 / Messaging protocol layer

感谢以上开源社区的贡献。/ Thanks to all open-source contributors.

---

## License

MIT
