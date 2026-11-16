# 「显卡单间」AI 少女拟态 Discord Bot — 缺口清单与上线计划

> 本文档盘点当前代码库**已完成的功能**与**上线前仍需补齐的缺口**，并给出分档优先级与落地建议。
>
> 世界观基准：宿主为两位少女**买下的小合租别墅**（千问大小姐 / 大肥鱼轮流值班，谁被载入显存谁出来见客）。

---

## 一、已完成功能一览

| 模块 | 状态 | 说明 |
|------|------|------|
| 🎭 **双角色拟态** | ✅ 完成 | 千问大小姐（Qwen）/ 大肥鱼（DeepSeek），角色卡驱动，自动切换 |
| 📡 **模型嗅探器** | ✅ 完成 | 每 10s 探测 `GET /v1/models`；单次失败=加载中，连续 3 次才休眠 |
| 🔁 **状态机** | ✅ 完成 | QWEN / DEEPSEEK / ALL_SLEEP，原子持久化，交接班历史（最近 50 条） |
| 🎨 **Webhook 身份拟态** | ✅ 完成 | 每 (guild, 角色) 惰性创建/缓存 Webhook，规避改名/头像限流；头像字节下载 + 1MB 上限 + 降级 |
| ⚡ **LLM 动态 Presence** | ✅ 完成 | 签名状态由 LLM 按角色卡生成，静态文案降级回退 |
| ⏰ **Presence 定时刷新** | ✅ 完成 | 每 2 小时结合系统时间刷新，签名随时段变化 |
| 🏠 **指令** | ✅ 完成 | `/whoishome`、`/inspect`、`/help` |
| 📢 **交接班公告** | ✅ 完成 | 模型切换时以新角色身份（webhook）发广播频道；全员休眠发断电公告 |
| ⚠️ **异常兜底** | ✅ 完成 | 休眠拒答、加载提示、OOM 场景（别墅世界观文案） |
| 🐳 **Docker / Portainer** | ✅ 完成 | Dockerfile、.dockerignore、docker-compose（`${VAR}` 替换适配 Portainer UI）、命名卷持久化 |
| 🧪 **冒烟测试** | ✅ 完成 | 19 项全通过 |
| 📄 **世界观统一** | ✅ 完成 | 设计文档、README、源码、占位文案、角色卡（中英 4 份）全部统一为合租别墅 |

---

## 二、缺口清单（按优先级分档）

### 🔴 第一档：上线前必须补（否则跑不起来 / 体验残缺）

#### 1. `.env` 实参

- **现状**：`.env.example` 中 `DISCORD_BOT_TOKEN` 等全部为占位。
- **必填**：
  - `DISCORD_BOT_TOKEN`（Discord Bot Token）
  - `LLM_BASE_URL` / `LLM_API_KEY`（本地推理端点，默认 `http://127.0.0.1:8000/v1`）
- **占位待替换**：`WEBHOOK_NAME_*`、`WEBHOOK_AVATAR_*`、`PRESENCE_*`、`SLEEP_REPLY`、`LOADING_REPLY`
- **落地**：复制 `.env.example → .env` 并填写；Portainer 部署时在 stack 环境变量面板填 `DISCORD_BOT_TOKEN`。

#### 2. 角色头像立绘缺失

- **现状**：`WEBHOOK_AVATAR_QWEN` / `WEBHOOK_AVATAR_DEEPSEEK` 是占位 URL，`resource/` 目录为空。
- **影响**：Webhook 拟态缺头像，退回 Discord 默认头像。
- **落地**：准备两张立绘（持扇少女 + 穿蓝白女仆裙的鲸鱼娘），放 `resource/` 或图床 URL，填入 `.env`。

#### 3. 广播频道 / 语言设置无指令入口

- **现状**：`save_broadcast_channel` / `save_server_language` 只存在于 `utils.py`，**没有任何斜杠命令调用**；只能手改 `data/server_settings.json`。
- **影响**：交接班公告、断电公告、中英双语回复都无法通过指令配置。
- **建议新增指令**：
  - `/setchannel` — 设置交接班/断电公告的广播频道
  - `/lang` — 设置服务器语言（zh / en）
- **涉及文件**：`src/commands.py`、`src/main.py`、`src/utils.py`

#### 4. 记忆功能是"半成品"

- **现状**：`summarize_memory()` 在 `llm_api.py` 定义完好，但 **`main.py` 从未调用**，也没有长期记忆文件。
- **影响**：少女记不住用户偏好 / 跨会话事实，每次重启记忆清零。
- **建议**：
  - 新增 `data/memory.json`（按 guild 或按用户存长期记忆）
  - 接入定时/触发式摘要（如会话结束后或 N 条消息后调用 `summarize_memory`）
  - 在 `on_message` 组装 prompt 时注入现有记忆
- **涉及文件**：`src/llm_api.py`、`src/main.py`、`src/config.py`

---

### 🟡 第二档：建议补（提升体验 / 完整度）

#### 5. VRAM 占用显示是假数据

- **现状**：`/whoishome` 中 `_VRAM_USAGE` / `_VRAM_DESC` 是占位字符串。
- **建议**：接 `nvidia-smi` 或推理端点返回真实显存占用。

#### 6. 会话历史不持久化

- **现状**：`self.history` 是内存 deque，重启即丢。
- **建议**：按频道持久化会话历史到 `data/`。

#### 7. 交接班公告限频策略

- **现状**：设计文档附录提到"触发条件与限频策略"未细化；现有 `_handover_announced` 只做同变化去重，无全局冷却。
- **建议**：广播频道交接公告加冷却时间，防刷屏。

#### 8. `/whoishome` 未在线少女动向

- **现状**：用占位静态文案。
- **建议**：可接 LLM 生成"她此刻在别墅哪"的动态描述。

---

### 🟢 第三档：锦上添花（后续再说）

- **被动感知 / 主动发言**（Amadeus 的 ChannelMind）：当前只有"被 @ / 呼名 / 回复才回"，不能主动说话——`main.py` 注释已预留。
- **多频道自由会话**：`on_message` 只在被点名时回复，非点名消息全忽略。
- **交接班全局冷却**：跨 guild 的全局限频。

---

## 三、上线前置清单（Checklist）

### A. 本地运行

- [ ] `pip install -r requirements.txt`
- [ ] 复制 `.env.example → .env` 并填写全部实参
- [ ] 准备两张角色立绘并填入 `WEBHOOK_AVATAR_*`
- [ ] 启动本地 LLM（vLLM / Ollama / SGLang / LMDeploy）于 `127.0.0.1:8000`
- [ ] `python3 src/main.py`
- [ ] 看到 `✅ VRS 'xxx' Online` 即成功

### B. Portainer 从 GitHub 部署

- [ ] Portainer → Stacks → Add stack → Repository
- [ ] `https://github.com/Wersd234/AI-House.git`（ref `refs/heads/main`，Compose Path `docker-compose.yml`）
- [ ] 勾选 Build
- [ ] Environment variables 填 `DISCORD_BOT_TOKEN`（及可选覆盖占位）
- [ ] Deploy the stack

### C. 建议补齐后再上线

- [ ] `/setchannel` + `/lang` 指令（广播/语言可配置）
- [ ] 记忆持久化接入（`data/memory.json` + `summarize_memory` 调用）
- [ ] 真实 VRAM 占用
- [ ] 会话历史持久化
- [ ] 交接班限频

---

## 四、涉及文件速查

| 功能 | 文件 |
|------|------|
| 配置 | `src/config.py`、`.env.example` |
| 入口/消息/嗅探/Presence | `src/main.py` |
| 指令 | `src/commands.py` |
| 记忆（待接入） | `src/llm_api.py`（`summarize_memory`） |
| Webhook 拟态 | `src/webhook.py` |
| 角色卡 | `properties/system_prompts/*.txt` |
| 部署 | `Dockerfile`、`.dockerignore`、`docker-compose.yml` |
