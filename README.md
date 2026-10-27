# AI Auto Streamer

AI 控制的轻量虚拟主播直播系统 · Lunaria Studio

Windows 完整启动步骤见 [START_HERE.md](START_HERE.md)。双击 **Start Studio.cmd** 可启动服务器并自动打开控制台与 HTML 输出。当前设置已保存为 `config.yaml` 的项目默认值，并保留在 `data/studio-defaults.json`。

## 当前版本：银龙控制室

启动 `python main.py`，打开 **http://127.0.0.1:8000/control**。
控制台包含实时形象预览、弹幕测试、回复记录、6 个自然动作和真实服务端状态。
默认使用用户参考图制作的透明银龙 2D 形象，30fps 上限；预览默认静音，可切换已有 VRM 3D 模型。
正式输出的角色渲染器由 `config.yaml` 控制，场景由控制台 **Scene** 设置。OBS 浏览器源地址为 **http://127.0.0.1:8000/?overlay=1&controls=0**。

默认 LLM 已配置为免费的 `glm-4-flash-250414`；请在项目 `.env` 中填写 `GLM_API_KEY`，保存后重启。设置步骤见 [GLM_SETUP.md](GLM_SETUP.md)。Edge-TTS 语音需要网络。
常见问候优先走模板，文字先显示、语音边生成边传到浏览器；弹幕窗口为 0.15 秒，回复冷却关闭，入口队列最多 100 条。已生成的相同台词和音色会缓存复用。

控制台顶部 **Current character** 可快速切换角色，**＋ New character** 可复制当前档案；每次启动默认 Astra。**Identity / Replies / Voice / Actions** 分别保存到当前角色，**Scene & music** 为全角色共用。面板可调整身份与称呼读法、回应方式与手动台词、音色试听、动作幅度与情绪映射，以及背景与构图。点击 **Apply & save** 立即生效，无需重启；设置保存在 `data/character-profiles.json`，包含所有角色与共用舞台，优先于 YAML；旧的 `studio-settings.json` 仅作兼容导出。现有设置已迁移到 Astra，新档案目前共用 Astra 画稿，独立外观需要额外素材。**Voice → Cool & composed** 使用晓晓女声、语速 -4%、音高 -3Hz、完整语句及关闭情绪变调。即使开启情绪变化，也只在所选声音上做轻微变化。**Replies → Low latency preset** 可恢复较早开口参数。外部模型和语音服务的网络等待仍会影响实际开口时间。

**Replies → 语音先念弹幕核心，再回答**：本地去掉昵称称呼、问候与链接，提取最近一条已选弹幕的短句（最多 32 字），加在首段回答前一起送入 TTS。模型回复、显示文字与回复记忆保持原样；不增加 LLM 调用。多条弹幕合并时只念最近一条，礼物感谢、进场欢迎和导演台词不加弹幕前缀。长消息使用有界原文摘取，不做语义摘要。

**可选付费声音**：Voice 中可选 MiniMax `speech-2.8-turbo` 及御姐、妩媚御姐、成熟女性系统音色。先在 `.env` 的 `MINIMAX_API_KEY=` 后填写密钥并重启，再试听/启用；未配置时保持免费 Edge 声音。MP3 音频流直接交给现有播放与口型链路，无需 GPU。MiniMax 音高使用独立的半音滑块，与 Edge 的 Hz 不混用。按[官方价格](https://platform.minimax.cn/docs/pricing/overview)，2026-10-26 为 ¥2/万计费字符，每个汉字按 2 字符计；试听也计费。已完成模拟流协议测试，尚未使用真实 MiniMax 凭证测量音质或延迟。

**GLM-TTS 已接入**：在 Voice 中选择 **GLM-TTS · 自然语音**，可试听彤彤、小陈、锤锤。直接复用 `.env` 中的 `GLM_API_KEY`，与 GLM 文本模型共用密钥；[官方价格](https://docs.bigmodel.cn/cn/guide/start/pricing)为 ¥2/万字符（2026-10-26），语音及试听与免费 GLM Flash 文本分开计费。支持语速、音量；当前接口未提供独立音高/情绪参数，所以 GLM 模式隐藏这两个控件。音频按块转换成 MP3 并送入原有播放/口型链路，使用轻量 CPU 编码，无需本地语音模型、GPU 或 FFmpeg。免费 Edge 仍可手动切换。

**Scene** 默认使用银龙座驾：窗外田园风景慢速循环，整幅画面轻微震动；可分别关闭，系统减少动态效果偏好下自动静止。角色坐姿、手握方向盘及伙伴位置保持固定。原有表情优化已按用户要求留待后续任务（`data/pending-tasks.json`）。其他场景提供华丽宫殿、蔷薇花园、月光露台和透明背景，适合全身站姿。金色微光、流云和细雨使用轻量 Canvas 动画（最高 20fps），也可选静态。DeepSeek 与 Claude 小伙伴为用户参考图衍生的静态鞠躬形象，可隐藏。**Bottom blur** 按钮与强度滑块控制底部渐变柔焦；透明背景自动隐藏天气、小伙伴与柔焦。拖动预览或用方向键调整角色组位置，Shift + 方向键大步调整，大小可设 40%–200%。更改先在编辑器预览，保存后同步到所有直播输出。点击 **Open full scene**，再点右上角 **Fullscreen** 全屏；OBS 建议 1440 × 1080（4:3）。强制纯透明角色输出可在 URL 加 `&transparent=1`。

**MiMo 声音风格控制**：在 `.env` 填写 `MIMO_API_KEY` 并重启，Voice 中选择 **MiMo · 自定义表达**。预置茉莉、冰糖、苏打、白桦音色；在 **Voice direction · 声音风格** 中用自然语言调整语气、语速、气声和情绪，留空则自然表达。这段说明单独传给 TTS，不加入字幕或播报文本。MiMo 模式隐藏不支持的数值语速、音高、音量和自动情绪控件。当前使用支持实时流式的 `mimo-v2.5-tts`，不使用需要等待全部生成完成的设计/克隆型号；24 kHz PCM 流通过轻量 CPU 编码复用已有 MP3 播放和口型。官方[定价](https://mimo.mi.com/docs/zh-CN/price/pay-as-you-go)在 2026-10-26 标注限时免费，后续以平台公告为准。**Cool & composed** 在 MiMo 下填入清冷、成熟、略带气声的风格起点；先试听再保存。

完整架构检查、已修复问题和运行限制见 [ARCHITECTURE.md](ARCHITECTURE.md)。
运行验证：`python -m unittest discover -s tests -v`、`npm test`、`npm run build:vrm`。

完全离线的形象与动作测试：`python main.py --config config.local-test.yaml`，打开 **http://127.0.0.1:8001/control**。
此配置无需 API Key、不会请求模型或语音服务。详细步骤见 [LOCAL_TEST.md](LOCAL_TEST.md)。
2D 演示仅保留待机、点头、思考、害羞、轻笑和轻迎六个自然的小动作。肩肘角度受限，双脚与裙摆固定，手腕与手掌保持连接；切换动作保留位置与速度。表情支持柔和、开心、害羞、思考和惊喜，并自动眨眼。口型由播放中的语音驱动，停顿、暂停或缓冲时闭嘴。在控制台点击 **2D Pose Studio**，或打开 **http://127.0.0.1:8000/rig-preview**，可预览动作、切换面部特写，并点击 **Test current voice** 测试当前音色与口型。动作与表情不调用模型；语音测试仅调用 TTS。

## 功能特性

- **弹幕采集**：支持 B站实时弹幕 + 文本输入测试模式
- **弹幕过滤**：时间窗口内多条弹幕智能选择，避免刷屏
- **回复间隔**：可配置冷却时间，控制回复频率
- **思考状态**：LLM 处理时显示思考动画
- **空闲行为**：长时间无弹幕时自动触发随机动作
- **平滑动画**：Live2D 参数插值，避免突跃
- **控制台**：独立控制面板，模拟弹幕输入
- **桌面覆盖层**：透明窗口，可置于桌面实时测试
- **LLM 思考**：支持 Ollama 本地 / OpenAI / DeepSeek 云端 API / 离线 Mock，流式输出
- **TTS 语音**：Edge-TTS 零成本语音合成，支持多种中文语音
- **动作映射**：情绪标签 → 渲染器无关的动作参数（口型/眼神/身姿/表情）
- **双渲染器**：Sprite 卡通贴图（默认）/ Live2D Cubism 4/5，配置切换，带口型同步
- **WebSocket 实时通信**：前端实时接收弹幕、回复、动作指令

## 系统架构

```
弹幕平台 ──→ 弹幕采集 ──→ 消息队列 ──→ LLM 思考 ──→ 回复文本
(B站/文本)   (WebSocket)  (asyncio)   (流式输出)      │
                                                      ↓
OBS 推流 ◄── 画面合成 ◄── Live2D 渲染 ◄── TTS 语音合成
            (透明背景)    (口型+表情)     (Edge-TTS)
```

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置 LLM

#### 方案 0：离线 Mock（无需任何 API，推荐先跑通）

`provider: "mock"` 使用内置的关键词情绪回复，无需联网或本地模型，适合先验证前端形象与动作：

```yaml
llm:
  provider: "mock"
```

#### 方案 A：Ollama 本地部署（推荐）

```bash
# 安装 Ollama: https://ollama.com
# 下载模型
ollama pull qwen2.5:7b-instruct
```

编辑 `config.yaml`：
```yaml
llm:
  provider: "ollama"
  base_url: "http://localhost:11434/v1"
  model: "qwen2.5:7b-instruct"
```

#### 方案 B：DeepSeek 云端 API

```bash
# 获取 API Key: https://platform.deepseek.com
```

编辑 `config.yaml`：
```yaml
llm:
  provider: "openai"
  base_url: "https://api.deepseek.com/v1"
  api_key: "sk-your-deepseek-key"
  model: "deepseek-chat"
```

### 3. 配置 TTS 与音色

默认使用 Edge-TTS 晓晓自然女声。优先在控制台 Voice 中试听和保存；也可以修改 YAML（已有控制台保存项会覆盖相同字段）：

```yaml
tts:
  voice: "zh-CN-YunxiNeural"  # 男声
  # 或
  voice: "zh-CN-XiaoxiaoNeural"  # 自然女声（默认）
  expressive: false            # 保持稳定的语速和音高
```

**可选情绪变调：** 默认关闭，避免忽高忽低。开启 Voice 中的情绪变调，或设置 `expressive: true` 后，才会按 LLM 情绪套用 `emotion_voice`；这些参数仅调整音色、语速、音高和音量。

```yaml
tts:
  voice: "zh-CN-XiaoxiaoNeural"
  expressive: true
  emotion_voice:                    # 情绪 → 覆盖（voice 省略则沿用默认音色）
    happy:     { rate: "+10%", pitch: "+8Hz",  volume: "+10%" }   # 更亮更快
    sad:       { rate: "-15%", pitch: "-6Hz",  volume: "-10%" }   # 更慢更低
    angry:     { rate: "+15%", pitch: "+4Hz",  volume: "+15%" }   # 更急更响
    surprised: { rate: "+12%", pitch: "+10Hz", volume: "+10%" }   # 上扬
    thinking:  { rate: "-10%", pitch: "+0Hz",  volume: "+0%" }    # 放缓
    normal:    { rate: "+0%",  pitch: "+0Hz",  volume: "+0%" }
```

想给某情绪换完全不同的音色，加 `voice` 即可，例如 `sad: { voice: "zh-CN-XiaoxiaoNeural", rate: "-15%" }`。

**控制台可试听音色：** `zh-CN-XiaoxiaoNeural`（女·默认）、`zh-CN-XiaoyiNeural`（女）、`zh-CN-YunjianNeural`（男）、`zh-CN-YunxiNeural`（男）。

### 4. 配置形象渲染器（Sprite / Live2D）

系统支持两种渲染器，通过 `live2d.renderer` 切换：

```yaml
live2d:
  renderer: "sprite"   # "auto" | "live2d" | "sprite"
  model_path: ""       # Live2D 模型路径（renderer=live2d/auto 时使用）
  scale: 0.3
  sprite_dir: "avatar" # sprite 贴图目录（相对 frontend/static）
```

- `sprite`：使用透明 PNG 卡通贴图（默认，零依赖，开箱即用）
- `live2d`：使用 Live2D Cubism 模型（需 `model_path`）
- `auto`：有 `model_path` 用 Live2D，否则回退 sprite

#### 使用银龙形象或内置 Claude 形象（Sprite）

当前默认银龙形象位于 `frontend/static/avatar_dragon/`，来自用户提供的三视图，使用透明分层绘画、受限肩肘与头部动作、自然眨眼和音频驱动口型。也保留了 Claude 主题卡通形象（珊瑚色头发 + 米白裙 + 星芒徽记）：将 `live2d.sprite_dir` 改为 `avatar_claude` 即可使用其原有姿态贴图；该旧形象尚无分层关节素材。

#### 替换为自己的卡通形象

1. 准备绿幕（纯绿背景）角色图，至少包含：`neutral`（闭嘴）、`talk`（张嘴）、以及各表情（`happy`/`sad` 等）
2. 放入 `frontend/static/avatar/raw/`
3. 运行预处理脚本（去绿幕 + 去水印 + 自动裁切）：

```bash
python scripts/preprocess_avatar.py frontend/static/avatar/raw frontend/static/avatar
```

4. 保持 `renderer: "sprite"` 即可。动作参数接口与渲染器无关，换形象无需改代码。

#### 使用 Live2D 模型

1. 下载 Live2D 模型（如 [Hiyori](https://www.live2d.com/en/learn/sample/)）
2. 解压到 `data/models/` 目录
3. 编辑 `config.yaml`：

```yaml
live2d:
  renderer: "live2d"
  model_path: "data/models/hiyori/hiyori.model3.json"
```

### 5. 配置弹幕过滤和回复间隔

编辑 `config.yaml`：

```yaml
danmaku_filter:
  enabled: true
  time_window: 2.0          # 时间窗口（秒），在此窗口内的弹幕会被收集
  strategy: "latest"        # 选择策略: "latest" | "random" | "priority"
  max_queue_size: 10        # 最大队列大小
  min_length: 2             # 最小弹幕长度
  max_length: 50            # 最大弹幕长度

reply_interval:
  cooldown: 3.0             # 回复间隔（秒）
  enabled: true
```

**弹幕过滤策略说明：**
- `latest`: 选择时间窗口内最新的弹幕
- `random`: 随机选择一条弹幕
- `priority`: 选择第一条符合条件的弹幕

### 6. 启动服务

```bash
python main.py
```

访问 http://127.0.0.1:8000 查看前端界面。

### 7. 使用控制台（推荐）

控制台提供独立的输入面板，方便测试：

```bash
# 访问控制台页面
# 浏览器打开 http://127.0.0.1:8000/control
```

控制台功能：
- 模拟弹幕输入
- 快捷消息按钮
- 实时弹幕记录
- AI 回复显示
- 系统状态监控（队列、冷却、空闲时间）

### 8. 桌面覆盖层模式

使用 pywebview 创建透明、置顶的桌面窗口：

```bash
# 安装 pywebview
pip install pywebview

# 启动桌面覆盖层
python desktop.py
```

桌面覆盖层特性：
- 透明背景（适合 OBS 捕获）
- 始终置顶
- 无边框窗口
- 可调整大小
- 同时打开控制台窗口

## 使用模式

### 文本输入模式（默认）

启动后在终端输入弹幕内容，系统会实时处理并显示回复。

```bash
python main.py
```

### B站弹幕模式

当前实现使用社区 `blivedm` 的网页端消息协议；它不是官方 Open Live 接入。2026-10-26 已改为固定的维护者源码版本，不能用 PyPI 的同名旧包。此工作区已在 `.venv` 安装测试依赖；启动和测试请使用 `.\.venv\Scripts\python.exe`。未配置直播间时保持 `platform.type: text`。

准备测试自己的直播间时，编辑 `config.yaml`：

```yaml
platform:
  type: "bilibili"
  room_id: 123456  # 替换为你的直播间 ID
```

再执行 `.\.venv\Scripts\python.exe main.py`。系统只监听直播间消息，不会替你开播、发送弹幕或购买礼物。直播画面和声音由 OBS 推流。网页端协议可能变化，匿名连接也可能得到打码昵称与 UID 0；尚未验证你的房间在线接收情况。

**官方 API 调研（2026-10-26）：** [直播开放平台文档](https://open-live.bilibili.com/document/f9ce25be-312e-1f4a-85fd-fef21f1637f8) 提供 `LIVE_OPEN_PLATFORM_DM`（弹幕）、`SEND_GIFT`（礼物，完整前缀相同）、`SUPER_CHAT`（付费留言）、`GUARD`（大航海）、`LIKE` 和 `LIVE_ROOM_ENTER` 等事件。官方消息中的数字 UID 已废弃，应使用 `open_id`；礼物单价单位为 1000 = 1 元，Super Chat 的 `rmb` 已是元，不能混用。

按[官方入门说明](https://open-live.bilibili.com/document/849b924b-b421-8586-3e5e-765a72ec3840)与[官方示例](https://github.com/bilibili-openplatform/OpenLive_CSharpDemo)，正式接入需要开发者权限、AccessKey ID/Secret、App ID 和主播身份码。密钥应仅放在本地环境变量或 `.env`，不要放在前端。当前项目尚未接入这些凭证；不能只填房间号就启用官方接口。

[官方应用生命周期](https://open-live.bilibili.com/document/eba8e2e1-847d-e908-2e5c-7a1ec7d9266f)是 `/v2/app/start` 获取场次与 WebSocket 鉴权信息，连接消息流，并约每 20 秒调用 `/v2/app/heartbeat`，退出时调用 `/v2/app/end`。这里的 start 是启动互动应用场次，不是开始视频直播。Open Live 与通用开放平台 OpenHome 使用不同的接入/鉴权体系，不能混用签名版本或事件名前缀。官方连接、权限、删除 Super Chat 的撤回处理、场次结束及真实推流仍需在后续接入中验证。

目前无需开播即可运行离线协议和本地接口测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_bilibili.py -v
```

测试包含网页端原始弹幕、普通/免费礼物、V2 Protobuf 礼物批次、入场与关注/分享的区分、多份大航海金额，以及本地 HTTP / WebSocket 到模拟回复与动作的流程。测试不连接 B站，不调用云端模型或 TTS；它不代表官方 API 鉴权或真实事件交付成功。

### Web 输入模式

访问 http://127.0.0.1:8000，在页面底部输入框发送弹幕。

## 情绪标签系统

LLM 回复会包含情绪标签，自动映射到 Live2D 表情：

| 情绪 | 标签 | 表情 | 动作 |
|------|------|------|------|
| 开心 | `[happy]` | 微笑 | 挥手 |
| 难过 | `[sad]` | 哭泣 | 低头 |
| 生气 | `[angry]` | 皱眉 | 跺脚 |
| 惊讶 | `[surprised]` | 睁大眼睛 | 后退 |
| 思考 | `[thinking]` | 眯眼 | 摸下巴 |
| 平静 | `[normal]` | 中性 | 待机 |

示例：
```
观众: 你好呀
AI: [happy] 你好你好！欢迎来到直播间～
```

## 6 个自然动作

当前正面立绘只开放能够自然完成的小动作。高举双手、捧心、遮脸、伸展与提裙礼已移出演示；旧设置会自动映射到保留的动作。

| 动作 | 中文 | 表现 |
|---|---|---|
| idle | 待机 | 双脚固定，微小头部与发丝运动 |
| nod | 点头 | 克制地点头 |
| think | 思考 | 轻微歪头、思考表情 |
| shy | 害羞 | 微微低头、柔和微笑与脸红 |
| laugh | 轻笑 | 开心眯眼、轻微点头，无弹跳 |
| present | 轻迎 | 单臂低位向外小幅展开 |

口型独立于动作，由真实音频的 RMS 音量和频谱驱动。它是轻量的音节节奏近似，不是逐音素识别。肩肘与头部过渡使用临界阻尼弹簧，输出角度再次限制，双脚不会漂移。语音不会经过身体的慢速平滑层。

控制台提供 6 个动作按钮；也可调用：

```bash
curl -X POST http://127.0.0.1:8000/api/test-event \
  -H "Content-Type: application/json" \
  -d '{"type": "action_test", "action": "shy"}'
```

## 思考状态

当 LLM 处理弹幕时，前端会显示"思考中..."动画：
- 紫色背景脉冲动画
- 思考表情图标
- 处理完成后自动切换到回复内容

## 空闲行为

当长时间无弹幕时，系统会自动触发空闲行为：

**配置：** `config.yaml` → `idle`

```yaml
idle:
  threshold: 30.0           # 空闲阈值（秒）
  enabled: true
  actions: ["normal", "thinking", "happy"]
  self_talk: false          # 是否自言自语
  self_talk_prompts:
    - "今天直播间好安静呀~"
    - "有没有人想聊聊天呢？"
```

**工作原理：**
- 超过 `threshold` 秒无弹幕，触发随机空闲动作
- 可选：使用 LLM 生成自言自语内容
- 防止直播间冷场

## 边界情况处理

### 1. 高频弹幕（很多人同时说话）

- **时间窗口过滤**：在 `time_window` 秒内收集所有弹幕
- **队列溢出保护**：超过 `max_queue_size` 时丢弃旧消息
- **智能选择**：根据策略选择一条回复（latest/random/priority）
- **回复间隔**：`cooldown` 秒内不重复回复

压测脚本（默认 20s 内注入 50 条弹幕，含过短/过长过滤用例）：

```bash
python scripts/stress_danmaku.py 50 20
```

### 2. 长时间沉默（没人说话）

- **空闲检测**：超过 `idle.threshold` 秒无弹幕
- **随机动作**：触发预设的空闲动作
- **自言自语**：可选生成主动对话内容
- **状态提示**：控制台显示空闲时间

### 3. 平滑动画过渡

- **参数插值**：使用 lerp 算法平滑过渡 Live2D 参数
- **避免突跃**：表情、动作切换自然流畅
- **口型同步**：基于音频音量实时驱动

## 目录结构

```
ai-auto-streamer/
├── main.py                 # 主入口
├── config.yaml             # 配置文件
├── requirements.txt        # Python 依赖
├── src/
│   ├── config.py          # 配置管理
│   ├── danmaku.py         # 弹幕采集
│   ├── llm.py             # LLM 处理
│   ├── tts.py             # TTS 引擎
│   ├── action.py          # 动作映射
│   ├── pipeline.py        # 流水线编排
│   └── server.py          # FastAPI 服务器
├── frontend/
│   ├── index.html         # 前端页面
│   ├── control.html       # 控制台页面
│   └── static/
│       ├── style.css      # 样式
│       ├── app.js         # 前端逻辑（渲染器抽象）
│       ├── control.js     # 控制台逻辑
│       └── avatar/        # 卡通形象透明贴图
│           ├── neutral.png / talk.png / happy.png / sad.png
│           └── raw/       # 绿幕原图（预处理输入）
├── scripts/
│   └── preprocess_avatar.py  # 绿幕→透明贴图预处理
└── data/
    ├── models/            # Live2D 模型
    └── audio/             # TTS 音频缓存
```

## API 接口

### WebSocket

- `ws://127.0.0.1:8000/ws` - 实时事件推送（与 HTTP 同端口）

事件类型：
```json
{
  "type": "response_start",
  "emotion": "happy",
  "action": { "mouthOpen": 0.6, "eyeOpen": 1.2, ... }
}
{
  "type": "response_chunk",
  "text": "你好"
}
{
  "type": "response_end",
  "audio_url": "/audio/tts_1234567890.mp3"
}
```

### REST API

- `GET /health` - 健康检查
- `GET /api/config` - 前端渲染器配置（renderer / model_url / sprite_dir / character）
- `POST /api/danmaku` - 发送弹幕
- `POST /api/test-event` - 触发测试事件（如 `idle_test`）

```bash
curl -X POST http://127.0.0.1:8000/api/danmaku \
  -H "Content-Type: application/json" \
  -d '{"content": "你好", "user_name": "测试用户"}'
```

## 性能指标

| 环节 | 目标延迟 |
|------|----------|
| 弹幕采集 + 过滤 | < 100ms |
| LLM 首句流式输出 | 1-2s |
| TTS 首包 | 100-500ms |
| 口型/动作触发 | < 100ms |
| **端到端总计** | **2-3s** |

## 进阶路线（Phase 2+）

- [ ] 多平台弹幕支持（抖音、快手、YouTube）
- [ ] GPT-SoVITS 声音克隆
- [ ] 情感分析自动驱动动作
- [ ] 实时 3D 渲染（UE5/VRM）
- [ ] 语音实时对话（ASR 集成）
- [ ] 记忆系统（长期对话上下文）

## 故障排查

### LLM 无法连接

```bash
# 检查 Ollama 是否运行
ollama list

# 检查 API 是否可达
curl http://localhost:11434/v1/models
```

### TTS 无声音

- 浏览器自动播放限制：首次需在页面上点击或按任意键解锁音频；OBS 浏览器源请勾选「允许自动播放」
- 检查浏览器是否允许自动播放音频
- 检查 `data/audio/` 目录是否生成了音频文件
- 确认音频请求为 `/api/voice-stream/tts_*.mp3` 或 `/audio/tts_*.mp3` 且返回 200/206（非 404）
- 打开 Voice，点击 Audition voice 检查试听；再开启预览声音测试直播回复

### Live2D 模型不显示

- 检查模型路径是否正确
- 检查浏览器控制台是否有错误
- 确认模型文件格式为 Cubism 4/5（`.model3.json`）

## 许可证

MIT

## 致谢

- [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) - 架构参考
- [AI-Vtuber](https://github.com/Ikaros-521/AI-Vtuber) - 弹幕采集参考
- [LiveTalking](https://github.com/lipku/LiveTalking) - 实时渲染参考
- [edge-tts](https://github.com/rany2/edge-tts) - TTS 引擎
- [pixi-live2d-display](https://github.com/guansss/pixi-live2d-display) - Live2D 渲染
