# 「显卡单间」AI 少女拟态 Discord Bot — 缺口清单与上线计划

> 本文档盘点当前代码库**已完成的功能**与**上线前仍需补齐的缺口**，并给出分档优先级与落地建议。
>
> 世界观基准：宿主为两位少女**买下的小合租别墅**（千问大小姐 / 小鲸鱼轮流值班，谁被载入显存谁出来见客）。

---

## 一、已完成功能一览

| 模块 | 状态 | 说明 |
|------|------|------|
| 🎭 **双角色拟态** | ✅ 完成 | 千问大小姐（Qwen）/ 小鲸鱼（DeepSeek），角色卡驱动，自动切换 |
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

- **现状**：✅ 已在 Portainer 填入 `DISCORD_BOT_TOKEN`、`LLM_BASE_URL=http://192.168.0.58:1234/v1`（局域网 Ollama，容器可达）、`LLM_API_KEY=123`。
- **已修复**：compose 的 `${VAR:-}` 会把未填项注入**空字符串**——旧代码 `os.getenv(key, default)` 拿到空串会**丢失默认值**（webhook 名变空导致 Discord 拒建）。现已新增 `config._env()` 空串安全读取，留空一律回退内置默认（名字默认「千问大小姐」「小鲸鱼」）。
- **仍待填**：两张角色立绘 URL（`WEBHOOK_AVATAR_*`，留空则用 Discord 默认头像）。
- **注**：Portainer 里现存的 `LLM_MODEL_NAME` / `TAVILY_API_KEY` / `PTV_DEVELOPER_ID` / `PTV_API_KEY` 是其他项目遗留，**本项目不使用**，可删除。

#### 2. 角色头像立绘 ✅ 已内置占位图标

- **曾现状**：`resource/` 为空，且代码只支持 URL 头像；`.gitignore` 还会忽略图片导致仓库/镜像里没有。
- **现已修复**：
  - `resource/qwen.png`（折扇图标）与 `resource/deepseek.png`（鲸鱼图标）已生成并**随仓库提交、随 Docker 镜像打包**（`tests/gen_avatars.py` 可重新生成）
  - `webhook.py` 头像加载支持 **http(s) URL 或本地路径**（相对项目根，1MB 上限）
  - 默认 `WEBHOOK_AVATAR_*=resource/*.png`，开箱即用
- **升级路径**：把真立绘 URL 填进 `WEBHOOK_AVATAR_*`，或直接覆盖 `resource/*.png` 两个文件（`.gitignore` 对应行已放开）。

#### 3. 广播频道 / 语言设置无指令入口 ✅ 已修复

- **曾现状**：`save_broadcast_channel` / `save_server_language` 只存在于 `utils.py`，无指令调用。
- **现已新增**：
  - `/setchannel <频道>` — 设置交接班/断电公告的广播频道（仅管理员）
  - `/lang zh|en` — 设置服务器语言（仅管理员，下拉选项）
  - `/help` 已同步列出

#### 4. 记忆功能是"半成品" ✅ 已修复

- **曾现状**：`summarize_memory()` 定义完好，但 main.py 从未调用，也无长期记忆文件。
- **现已接入**：
  - `data/memory.json`（按服务器存长期记忆，Docker 卷持久化）
  - 每 `MEMORY_EVERY_N_MESSAGES`（默认 10）条用户消息，后台把该频道最近对话摘要合并进记忆（per-guild 锁防并发）
  - 每次回复前以 `[长期记忆·别墅住户档案]` 注入 system prompt，少女跨重启记得住户偏好
  - 开关：`MEMORY_ENABLED`（默认 true）

---

### 🟡 第二档：建议补（提升体验 / 完整度）

#### 9. QQ→Discord 迁移方案落地清单 ✅ 已全部实现（2026-11-17）

> 落地补充：reaction 通过 JSON 新增可选 `"reaction"` 字段回传（引擎清洗后 `add_reaction`）；
> 表情名录/表情包库存自动注入 system prompt；偷图自动存数据卷 `data/stickers/`。

**✅ 已实现（无需再动）**

| 迁移文档条目 | 本项目现状 |
|---|---|
| send_message 数组分条 + 限流 | `_split_bubbles`（≤4 条）+ 每条间 0.8~1.5s 随机延迟，天然低于 5条/5s |
| 发送前 sendTyping | `async with message.channel.typing()` 已在生成前触发 |
| 上下文按 channel_id/Thread 隔离 | 历史与记忆批次都以 `channel_id` 为键，Thread 自带独立 id，公告区不会串进水区 |
| Markdown 禁长列表/标题 | 两张卡黑名单已禁；Discord 会渲染所以禁得对 |
| 删掉「合并转发看不了」免责条款 | 我们的卡从未写入该条款 |
| MESSAGE CONTENT INTENT | README 部署清单已列为必开项 |

**✅ 已改造（9.1~9.8，本次完成）**

| # | 项目 | 改动点 |
|---|---|---|
| 9.1 | **`<@id>` / `<:emoji:id>` 预处理**（文档坑3） | 喂 LLM 前把 `<@!?(\d+)>` 替换为 `displayName > globalName > username`，`<a?:name:id>` 替换为 `:name:` 文本；否则模型会念数字 ID。新增 `utils.prettify_mentions()` |
| 9.2 | ~~静默引用回复~~ ⚠️ 已降级 | **实测结论**：Discord incoming webhook 原生不支持回复/引用（discord.py 2.7.1 `Webhook.send` 无 `reference`；discord-api-docs#3282 自 2020 未实现）。已改为：webhook 气泡直接落在唤醒消息下方（观感即接话），`allowed_mentions` 全程静音防误 @；native quote 仅在 webhook 不可用的 bot 兜底路径生效。彻底实现需等 Discord 开放或改用 button-interaction 方案 |
| 9.3 | **Reaction 回应（拍一拍替代）** | 扩展 LLM 输出 JSON 为 `{"emotion","reply","reaction"}`（reaction 可选，单个 emoji）；引擎 `message.add_reaction()` 贴上。Prompt 卡同步加"敷衍/附和无语时贴一个表情，别刷一排"条款 |
| 9.4 | **SERVER MEMBERS INTENT** | 9.1 解析别人昵称需要成员数据；Developer Portal 开启 + intents.members=True（README 清单补一行） |
| 9.5 | **分条上限 4→3**（文档建议 2~3 条） | `_split_bubbles(max_bubbles=3)`，一行改动 |
| 9.6 | **requirements 升 `discord.py>=2.4`** | message_snapshots（9.7）需要 2.4+ |
| 9.7 | **转发消息 message_snapshots**（映射表第7行） | `on_message` 读 `message.message_snapshots`，拼 `[转发·来自xx]\n正文` 注入 user_text；快照里的图片同时进视觉管线 |
| 9.8 | **Discord 特色 Markdown 放行**（文档坑4） | 卡黑名单微调：允许 `||剧透||`、`-# 小字吐槽` 偶尔用（zh/en 四张卡各一句） |

**✅ 已新做（9.9~9.11，本次完成；9.11 为 MVP：自动偷图+名录+[sticker:tag] 发图）**

| # | 项目 | 说明 |
|---|---|---|
| 9.9 | **媒体源扩面**（映射表 get_message_images 行） | 现在只读 `attachments`。补：① `embeds` 里的图片/`image.url`（Tenor/Giphy 动图常走这条）；② 文本内 `<a?:name:id>` → `https://cdn.discordapp.com/emojis/{id}.png` 进视觉管线；③ `message.stickers`——**只取 `format_type` 为 PNG/GIF 的**（`sticker.url` 自带 png 预览，Lottie/JSON 动画过滤掉不喂视觉模型） |
| 9.10 | **服务器自定义 Emoji 名录注入** | 每 guild 缓存 `name→<:name:id>` 名录（前 N 个）注入 system prompt，模型才会用本服表情回话（Bot 在自己所在服务器发的消息可随意使用任何服务器 emoji，无需 Nitro） |
| 9.11 | **表情包体系（send_sticker/list/collect 的 DC 平替）** | 本地 `resource/stickers/` + manifest（tag/描述）；回复里出现 `[sticker:tag]` 占位符→引擎把对应图片作为附件发出；`collect_sticker` 平替=识别到别人发的图/emoji/sticker 时自动下载入库并写 manifest。放第三档 |

#### 10. VRAM 占用显示是假数据

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

- [ ] **Discord Developer Portal → Bot → Privileged Gateway Intents 全部打开**：
      `MESSAGE CONTENT INTENT`（否则收不到正文/转发快照）**与 `SERVER MEMBERS INTENT`**
      （2026-11-17 起必需：解析 `<@id>` 为服务器昵称，缺失会启动报 PrivilegedIntentsRequired）
- [ ] 邀请链接权限含 **Manage Webhooks**（`permissions=536955904`）
- [ ] Portainer → Stacks → Add stack → Repository
- [ ] `https://github.com/Wersd234/AI-House.git`（ref `refs/heads/main`，Compose Path `docker-compose.yml`）
- [ ] 勾选 **Rebuild the image**（更新代码就靠它；**不要勾 Re-pull image**，本地构建镜像会报 pull access denied）
- [ ] Environment variables 填 `DISCORD_BOT_TOKEN`（及可选覆盖占位）
- [ ] Deploy the stack

### C. 建议补齐后再上线

- [x] `/setchannel` + `/lang` 指令（广播/语言可配置）— 已完成
- [x] 记忆持久化接入（`data/memory.json` + `summarize_memory` 调用）— 已完成
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
