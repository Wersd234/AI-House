# 🏠 显卡单间（VRAM Suite）— AI 少女拟态 Discord Bot

一间"只能容纳一张床位的显存单人间"。千问大小姐（Qwen）与大肥鱼（DeepSeek）轮流值班，谁被载入显存，谁就出来见客。宿主在后台切换模型，Bot 自动嗅探变身，用户没有切换权。

基于 **Project Amadeus** 的分层框架改造（config / llm_api / prompts / commands / utils 架构一脉相承）。

---

## ✨ 功能一览

| 模块 | 功能 |
|---|---|
| 🎭 **双角色拟态** | 千问大小姐（Qwen） / 大肥鱼（DeepSeek），按当前载入模型自动切换 |
| 📡 **心跳嗅探器** | 每 10 秒探测 `GET /v1/models`，识别当前运行的 Model ID |
| 🔁 **状态机** | 活跃角色状态机（QWEN / DEEPSEEK / ALL_SLEEP），含交接班历史 |
| 🎨 **Webhook 拟态** | 发言通过频道 Webhook 伪造对应角色的名字 + 头像（规避改名限流） |
| 🏠 **状态看板** | `/whoishome` / `/inspect` 查看当前谁在显卡里 |
| 🔌 **断电休眠** | 本地服务未启动时，Bot 以"机箱断电"文案拒答 |
| ⚡ **交接班公告** | 检测到模型切换时，以新角色身份在指定频道发交接动态 |

---

## 🚀 快速启动

### 1. 配置 .env

```bash
cp .env.example .env
```

至少填：

```env
# Discord
DISCORD_BOT_TOKEN=你的Discord机器人Token

# LLM 后端（OpenAI 兼容，vLLM / Ollama / SGLang / LMDeploy）
LLM_BASE_URL=http://127.0.0.1:8000/v1
LLM_API_KEY=123
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动

```bash
python3 src/main.py
```

看到 `✅ VRS 'xxx' Online | 当前当值: ...` 即成功。

---

## 🎭 世界观与角色

| 角色 | 活跃触发条件 | 角色定位 | 典型行为 |
|---|---|---|---|
| **千问大小姐**（Qwen） | 探测到模型 ID 含 `qwen` | 屈尊下凡的千金贵族，傲娇体面爱挑剔 | 嫌弃机箱散热差、风道灰尘多，但维持名媛气派 |
| **大肥鱼**（DeepSeek） | 探测到模型 ID 含 `deepseek` | 霸占水池的鲸鱼女仆，贪吃摸鱼逆鳞护体 | 自称"水冷管就是我的水族箱"，逆鳞是绝不许说她胖 |
| **全员休眠** | 无推理服务在线 | 宿主关机 / 停电 | 展示"机箱断电中，少女们都睡着了" |

**绝对规则**：严禁用户切换角色。谁在显卡里醒着，谁就出来见客。

---

## 📡 模型嗅探（Model Sniffer）

`src/sniffer.py` 后台常驻轮询（默认每 10 秒）：

- 探测 `GET http://127.0.0.1:8000/v1/models`
- 模型 ID 含 `qwen` → **千问当值**
- 模型 ID 含 `deepseek` → **肥鱼当值**
- 连接拒绝 / 超时 → **全员断电休眠**

状态变化触发：更新 Discord Presence + 可选交接班公告。

---

## 🎨 Webhook 身份拟态

`src/webhook.py` 为每个 (guild, 角色) 惰性创建/缓存一个频道 Webhook：

- **千问**：发送名 + 高雅傲娇持扇少女立绘
- **大肥鱼**：发送名 + 顶着头鳍、穿蓝白女仆裙、抱大饭碗的鲸鱼娘立绘

规避 Discord 改名/改头像的严重 API 频率限制。

---

## 🏠 指令

| 指令 | 说明 |
|---|---|
| 💬 `@Bot` | 聊天（必定回复当值少女） |
| 🏠 `/whoishome` | 查看当前谁在显卡里（Rich Embed） |
| 🔍 `/inspect` | 视察显卡公寓（同 /whoishome） |
| ❓ `/help` | 指令列表 |

---

## 📁 项目结构

```
AI合租房/
├── src/
│   ├── main.py          # Discord 客户端入口 + 嗅探循环 + 状态机 + Webhook 发送
│   ├── config.py        # 全部配置常量
│   ├── sniffer.py       # 心跳嗅探器（GET /v1/models）
│   ├── state.py         # 活跃角色状态机 + 交接班历史
│   ├── llm_api.py       # LLM 接口（OpenAI 兼容）
│   ├── prompts.py       # 角色卡装配器
│   ├── webhook.py       # Webhook 身份拟态引擎
│   ├── commands.py      # 斜杠指令（/whoishome /inspect /help）
│   └── utils.py         # JSON 持久化 + 启动检查
├── properties/system_prompts/
│   ├── qwen_zh.txt      # 千问大小姐人设
│   ├── qwen_en.txt
│   ├── deepseek_zh.txt  # 大肥鱼人设
│   └── deepseek_en.txt
├── resource/            # 立绘占位
├── data/                # 运行时数据（状态机 / webhook 缓存）
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🔧 占位参数

所有"左右参数"（角色名、头像 URL、Presence、文案）目前均为 **占位符**，集中在：

- `.env`（见 `.env.example` 的 `WEBHOOK_*`、`PRESENCE_*`、`SLEEP_REPLY`、`LOADING_REPLY`）
- `src/commands.py` 的 `_CHAR_DISPLAY` 和 `_VRAM_USAGE`
- `src/config.py` 的 `WEBHOOK_IDENTITY`、`PRESENCE_BY_CHARACTER`

部署前替换即可。

---

## 🐳 Docker（可选）

```bash
docker compose up -d --build
```

`LLM_BASE_URL` 需改为 `http://host.docker.internal:8000/v1`（容器访问宿主机）。
