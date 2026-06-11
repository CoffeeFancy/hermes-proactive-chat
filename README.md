# Hermes Proactive Chat 🤖

让 AI 助理学会**主动找用户聊天**，而不是永远被动等用户开口。

## 这解决了什么问题？

大部分 AI 助理/聊天机器人都是 **「你问我答」** 模式——用户不说话就死寂。
但真正像人的对话，AI 也应该能：
- 看到用户在忙某个事，主动吐槽一句
- 发现一个有意思的东西，分享给用户
- 之前聊到一半的话题，过一会儿补一句后续想法

这个项目就是做这个的。

## 怎么工作？

```
每 5 分钟触发一次
  │
  ├─ 安静时段？         → 跳过（默认 23:00~07:30）
  ├─ 还在冷却期？        → 跳过（发完后随机冷却 5~90 分钟）
  ├─ 用户最近有消息？     → 跳过（不打扰正在聊天的用户）
  │
  ├─ 概率衰减
  │   连续未回复 0 次 → 60% 概率触发
  │   连续未回复 1 次 → 30%
  │   连续未回复 2 次 → 15%
  │   连续未回复 ≥4 次 → 3% （基本闭嘴）
  │
  └─ LLM 决定
       DeepSeek 看当前上下文 + 时间 → 决定发不发、发什么
       原则：不问候、不废话、直接抛观点/吐槽/分享
```

### 核心机制：概率衰减

> **如果你连续不回，AI 会越来越不想打扰你。**
> 一旦你回了一句，立刻重置到最活跃状态。

这是关键设计——AI 不是定时炸弹，它懂得看眼色。

## 快速开始

### 依赖

- **Python 3.8+**（只用标准库，零 pip 依赖）
- **Hermes Agent**（用于消息投递，[Hermes](https://github.com/NousResearch/hermes)）
- **DeepSeek API Key**（用于 LLM 决策，[注册](https://platform.deepseek.com/)）

### 安装

```bash
git clone https://github.com/CoffeeFancy/hermes-proactive-chat.git
cd hermes-proactive-chat

# 配置 API Key
cp .env.example .env
# 编辑 .env，填入你的 DEEPSEEK_API_KEY
```

### 运行

```bash
# 手动测试
python3 proactive_send.py

# 用 cron 每 5 分钟跑一次
crontab -e
# 添加：
*/5 * * * * cd /path/to/hermes-proactive-chat && python3 proactive_send.py >> run.log 2>&1
```

### 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `DEEPSEEK_API_KEY` | (必填) | DeepSeek API Key |
| `PROACTIVE_DELIVER_TARGET` | (必填) | Hermes send 目标 |
| `PROACTIVE_ACTIVE_THRESHOLD` | `600` | 用户活跃阈值（秒） |
| `PROACTIVE_QUIET_START` | `1380` | 安静时段开始（23:00） |
| `PROACTIVE_QUIET_END` | `450` | 安静时段结束（07:30） |

## 状态文件

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

## 系统提示词

AI 的主动行为由一段**小型人格提示词**控制。当前提示词使用「小墨」人格——一个只在有必要时才开口说话的助理。你可以修改 `SYSTEM_PROMPT` 来调教出你想要的行为。

核心约束：
- **不问候**（不喊"喂""嗨""在忙吗"）
- **不废话**（直接抛观点）
- **不假装是回答**（就是主动提的）
- **每次措辞不同**（不说套话）

## 自己改提示词试试

找 `proactive_send.py` 里的 `SYSTEM_PROMPT`，替换成你想要的人格设定试试效果。比如：

```python
SYSTEM_PROMPT = (
    "你是一个毒舌但靠谱的老朋友……"
)
```

## 致谢 / 来源

本项目源自 [**Open-LLM-VTuber**](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) 项目，摘取了其中的主动对话调度思路和 LLM 自主决策模式，并参考了 [**AllenReder**](https://github.com/AllenReder) 的 [**hermes-active-message**](https://github.com/AllenReder/hermes-active-message) 项目改造而来。

此外借用了以下项目的部分代码和设计思路：
- [Hermes Agent](https://github.com/NousResearch/hermes) — 消息投递通道与 cron 调度框架
- QQ Bot / OpenClaw — 消息收发底层协议适配

感谢以上开源社区的贡献。

## License

MIT
