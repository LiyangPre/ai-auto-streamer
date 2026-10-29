# 歌曲模块需求与制作流程

更新：2026-10-28。本文是 Songs 模块的需求、当前实现边界和操作流程；角色、人设、声音、自然表情、口型、安全及模块生命周期等共用能力见 [requirement_basic.md](requirement_basic.md)。日常启动见 [START_HERE.md](START_HERE.md)，扩展接口见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 目标与当前状态

Songs 播放预先制作、试听并保存的完整录音。下载、分离、声音转换和歌词匹配在开播前完成，直播播放只读本地文件，不等待在线 TTS 或转换模型。

- 首选上传现成歌曲 MP3，或将原始演唱转换为当前角色的声音后导入。转换沿用原演唱的旋律、音高及时间轴；音色接近程度仍需试听，不能保证完全等同于说话音色。
- 当前离线批处理是 **Astra 专用**；歌曲库、点歌策略和模块外观按角色保存。其他角色可以上传自己的录音，但通用的多角色转换向导尚未实现。
- 当前五首转换均已完成，使用 50 steps。**起风了、小幸运**可播且已有默认同步歌词；**告白气球、海屿你、大鱼**暂时禁播。禁播保留本地文件，不等于删除录音。
- 2026-10-28 另启动三首新录音制作：**I Really Want to Stay at Your House**（`BV1FN411n7FT`）、新版**海屿你**（`BV14GovBnESm`）、**泡沫**（`BV1y8411i7Dd`）。复用已批准声音，50 steps，请求并实际启动 3 个转换 worker；先分离各自人声，完成后独立转换。状态与日志见 `data/songs-converted/batches/20261028-new3/`；这些新录音仍在制作，未自动播放。
- MiMo `mimo-v2.5-tts-voicedesign` 用于当前说话声音及参考音色，不用于唱歌。旧实验区调用独立的 preset `mimo-v2.5-tts`；它不提供本项目可控制的逐音符乐谱，也不保证指定歌曲的旋律、节拍或完整歌词，不能作为准确翻唱的主要路径。

## 模块需求

| 项目 | 要求及当前行为 |
|---|---|
| 输入来源 | 接受有权使用的本地录音；Bilibili 路径记录具体 BVID、版本和来源 URL。公开视频可访问不代表其下载、改编或直播使用自动获得许可。 |
| 声音一致性 | 先确定并试听角色声音，再保存批准的参考 WAV、声音描述及 SHA-256。原音频或当前声音变化时停止旧批次，避免混用声音。 |
| 歌曲制作 | 原始录音 → 人声/伴奏分离 → 只转换人声 → 对齐混音 → 试听 → 入库；保留原始文件和各阶段证据。 |
| 质量优先 | 批处理默认 50 diffusion steps；允许请求最多 12 个转换 worker，实际并发受曲目数、内存限制。steps 是推理迭代数，不是训练轮次；worker 更多不保证更快。 |
| 中断恢复 | 只复用源文件、参考声音、参数和校验值一致的缓存；保留已完成分块。状态文件、转换音频和导出元数据采用受控发布，不把半成品当成完成曲目。 |
| 上台播放 | 一个有限演出独占舞台；准备/上传/导入不自动开唱。播放结束返回 Chat，Stop、旧回执、重连不得结束错误的演出。 |
| 音画同步 | 整曲 MP3 是可听主轨；可选静音人声 guide 驱动口型。歌词、表情和视觉节拍跟随播放时钟，缓冲、暂停或静音时不得假唱。 |
| 点歌与禁播 | 只从当前角色已完成、未禁播曲目中匹配；有界队列、观众及同曲冷却，不能因弹幕启动下载、转换或 TTS 制作。 |
| 共用舞台 | 场景素材、DeepSeek/Claude 娃娃和聊天 BGM 可复用；模块 `scene_preset` 可临时覆盖场景，结束恢复 Chat 的共用场景。演唱时按选择暂停 BGM、保留氛围音乐，或播放已对齐伴奏。 |
| 表情与动作 | 使用基础表情引擎的同一套物理约束、眨眼和音频口型；模块只覆盖姿态、表情类型、强度和视觉节拍，不复制一套面部实现。 |
| 备份 | 备份歌曲音频、guide、元数据和已连接歌词；原录音、伴奏、角色配置和制作环境分别备份。不得将 API 密钥加入歌库导出。 |

## 环境与准备条件

所有 PowerShell 命令均在项目根目录执行：

```powershell
Set-Location 'D:\ai auto streamer'
```

| 环境/目录 | 用途与检查点 |
|---|---|
| `.venv` | 直播服务、编排、混音导出及歌库导入；依赖见 `requirements.txt`。 |
| `.venv-download` | 下载，需 `bili-dl` 的 `yt-dlp` 依赖和 `imageio-ffmpeg`；不修改直播环境。 |
| `.venv-svc` | 已配置的离线转换环境，Seed-VC v1、Demucs 4.1.0、Torch/torchaudio 2.4.1 CPU；与直播环境隔离。 |
| `.tools/seed-vc` | 固定版本 Seed-VC 工作副本、已缓存模型及本地 CPU/续算适配。批处理验证的上游 commit 为 `51383efd921027683c89e5348211d93ff12ac2a8`。 |
| `.tools/demucs-cache` | 分离模型缓存；`--offline` 不下载缺失模型。 |
| `.venv-lyrics` | 可选离线歌词识别/匹配，版本由 `requirements-lyrics.txt` 固定。 |

当前机器已准备好以上制作环境。新机器安装主项目依赖不会自动安装转换模型，也不会还原 `.tools` 中的本地适配；仓库尚无一键重建完整转换环境的安装器。迁移时需按上游安装说明和本机 `data/qa/seed-vc-setup.json`、`data/qa/demucs-setup.json` 重建并先做短片段验证；不要仅安装未经适配的上游版本就启动整批。

只检查当前环境/清单，不进行转换：

```powershell
.\.venv\Scripts\python.exe scripts/convert_song_voice.py --status --reference data/voice-references/astra-current-voice.wav
.\.venv-svc\Scripts\python.exe -X utf8 scripts/seed_vc_batch.py --validate-only
.\.venv\Scripts\python.exe scripts/prepare_astra_songs.py --status
```

`--status` 不证明旧进程仍在运行，先看状态、更新时间和报错；不要用全局结束 Python 或删除所有锁的方式恢复制作。

## 1. 获取正确版本的录音

### Bilibili 下载

先人工搜索并确认歌曲、演唱者、版本及 BVID。当前项目脚本**不实现搜索**；它使用 [bili-dl](https://github.com/Echoziness/bili-dl) 的 `yt-dlp` 引擎匿名下载可访问的音轨，再用 `imageio-ffmpeg` 导出 192 kbps MP3，不读取浏览器 Cookie 或下载视频。不要把登录凭证放入来源清单。

新环境可先建立下载环境；已有环境无需重复安装：

```powershell
py -3.12 -m venv .venv-download
.\.venv-download\Scripts\python.exe -m pip install bili-dl imageio-ffmpeg
```

建立 UTF-8 JSON 数组，例如 `data/song-sources/requested-allowed.json`，每项只需 `bvid` 和 `title`：

```json
[
  {"bvid": "BV1D64y1Z7Ko", "title": "起风了-买辣椒也用券"},
  {"bvid": "BV1y82uB2ET4", "title": "小幸运"}
]
```

```powershell
.\.venv-download\Scripts\python.exe -X utf8 scripts/download_bilibili_song_sources.py --manifest data/song-sources/requested-allowed.json --output data/song-sources
```

`data/song-sources/download-results.json` 保存成功/失败、来源 URL、原始音轨、MP3 路径和时长。脚本会跳过该结果清单里已成功且 MP3 仍存在的 BVID；这不是完整的文件损坏检测，后续制作必须检查实际解码和 SHA-256。下载脚本固定走直连，不读取 Studio 的模型代理设置；访问失败不代表 TTS/LLM 密钥失效。需要登录或无法匿名访问的内容会失败，不绕过限制。

### 本地文件

已完成的整曲可以直接在 **Songs → Upload song MP3** 上传，不必经过下载/转换。希望转换本地原唱时，保留原文件并先分离人声，使用下述低层转换包装器；当前 BVID 批处理清单及导入器绑定 Bilibili 来源，不能为本地歌曲虚构 BVID。通用本地来源清单/一键制作属于后续工作。

## 2. 确定并批准角色声音

1. 在当前角色 **Voice** 中保存声音，试听说话片段，确认音色、音高感和表达；先决定声音，再开始长时间制作。
2. 当前批准参考是 `data/voice-references/astra-current-voice.wav`，配套 JSON 保存 `approved`、`candidate_id`、`profile_id`、`style`、`model`、文件路径及 `reference_sha256`。现有参考为 18.936 秒、24 kHz 单声道 PCM16，批处理使用前 8 秒。
3. 新参考必须实际试听并批准，再更新清单；不可只改描述或复制 `approved: true` 来表示已经试听。现有脚本没有通用参考录制/批准向导，此步骤目前由人工维护。
4. 用真实分离人声做约 4 秒转换试听，检查音色、音准、尾音和杂音，再制作完整歌曲。技术数值检查不能替代听感验收。

编排器持续检查当前 Astra 的 MiMo provider、model、style 以及批准参考哈希。保存了新声音后，旧批次会暂停；已导入录音仍是其制作时的声音，不会随 Voice 设置自动变化。

## 3. 建立并核对制作清单

`data/songs-converted/sources.json` 是本次分离输入，采用下载结果条目的结构；只保留本次实际要处理且 `ok: true` 的录音。`jobs.json` 是转换输入，顶层包含：

- `reference`、`reference_sha256`、`reference_metadata`、`voice_candidate`。
- `diffusion_steps: 50`、`max_workers: 12`。
- `tracks`：每曲的 `id`/`bvid`、`title`、`source_url`、`source`、`source_sha256`、`source_duration`、`vocals`、`accompaniment`、`output_dir`，以及相同参考的路径/哈希/元数据路径。

源文件必须位于 `data/song-sources`；批处理转换的源、参考和输出路径必须在本工作区内，单曲输出位于 `data/songs-converted/<BVID>/`。`source_duration` 应对应这份音轨，完整解码后的样本数是后续校验依据，不能直接信任页面显示的整秒时长。

当前没有自动建立任意新批次清单的通用向导；新增录音时需核对现有结构、文件哈希和输出目录，并运行 `seed_vc_batch.py --validate-only`。不要在运行中的批次修改来源/参考或复用不同录音的输出目录。

**禁播范围需区分：** 当前点歌、手动播放和离线歌词识别/匹配工具会检查角色禁播策略；UI 上传 LRC 本身不解除禁播。离线编排器直接读取 `jobs.json`，不会自动过滤禁播歌。当前五首都已完成，无需重新运行；新建制作清单也只放本次获准处理的曲目，不因禁播而重新转换已有文件。

## 4. 分离、转换、混音与恢复

日常使用一条编排命令；它按清单分离、转换、导出并导入，不自动播放：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/prepare_astra_songs.py --steps 50 --workers 1 --threads-per-worker 6
```

`--workers` 可填 1–12；实际值还受歌曲数量及可用内存限制。当前 CPU 机器已验证一转换 worker、六线程的配置，额外还有四线程分离器。提高并发前先测吞吐和内存；不可用更多 worker 来宣称同等延迟或更高音质。

另一个终端可查看状态或安全请求停止，再运行同一开始命令续作：

```powershell
.\.venv\Scripts\python.exe scripts/prepare_astra_songs.py --status
.\.venv\Scripts\python.exe scripts/prepare_astra_songs.py --stop
```

实现分为三个阶段，以下命令用于排查/独立阶段操作，**不要与编排器重复运行**：

```powershell
.\.venv-svc\Scripts\python.exe -X utf8 scripts/separate_song_sources.py --manifest data/songs-converted/sources.json --offline --threads 4 --benchmark-seconds 0
.\.venv-svc\Scripts\python.exe -X utf8 scripts/seed_vc_batch.py --steps 50 --threads 6 --watch
.\.venv\Scripts\python.exe -X utf8 scripts/export_converted_songs.py --steps 50
```

- 分离器用 Demucs `htdemucs` 输出 44.1 kHz FLOAT WAV；非人声 stem 相加形成伴奏，保留共同时间轴。禁止各自裁掉开头静音，否则混音和歌词都会错位。
- Seed-VC 只转换 `vocals.wav`，使用 F0 conditioning，`auto_f0_adjust=false`、`length_adjust=1.0`、`semitone_shift=0`、CPU float32。输出整段人声，分块和重叠缓存用于中断恢复，不训练新模型。
- 导出器检查来源/参考/转换哈希、完整样本数及长度误差，抑制静音间隙噪声、调整人声增益，再与原伴奏混合并限幅；导出整曲及静音口型 guide。偏移超过允许范围会拒绝，不用时间拉伸掩盖转换错误。
- 同一源文件、参考和参数可复用完成结果；不同 steps、参考或来源需要重新验证/生成。`--force` 是低层工具的显式重做选项，不是常规恢复方式。

### 升调的边界

`scripts/convert_song_voice.py` 是单文件低层包装器，支持 `--semitones -12..12`，实际在转换推理中改变目标 F0，仍需重新生成；不是把现有人声瞬时升调的播放器功能。默认参考路径是历史文件，使用当前声音必须显式传入：

```powershell
.\.venv\Scripts\python.exe scripts/convert_song_voice.py --source data/songs-converted/BV1D64y1Z7Ko/vocals.wav --reference data/voice-references/astra-current-voice.wav --steps 50 --semitones 2 --output output/song-pitch-preview --plan
```

`--plan` 只打印计划，不转换。试听确认后移除该参数才执行。包装器不提供批处理的续算、混音、批准校验或歌库导入；升调后的试验输出不能冒充零升调批次结果。当前批处理固定零升调，没有“保留时间轴直接快速升降调”的成品后处理/UI；需要的话另行实现并校验音质、伴奏调性、guide 与歌词。

## 5. 试听、导入与备份

完整试听至少覆盖开头、主歌、副歌、高音、换气、间奏及结尾，检查漏字、错调、尾音噪声、接缝、伴奏串入和削波。保持原始混音可回退。

编排器生成完整 `prepared.json` 后，通过运行中 Studio 的本地 `/api/singing/import-prepared` 校验当前 Astra 声音并导入；服务拒绝连接时会尝试离线导入。独立离线导入必须**先停止 Studio**：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/import_prepared_songs.py --manifest data/songs-converted/prepared.json
```

导入验证批准参考、BVID/URL、MP3 格式、时长与哈希；同一转换版本再次导入会复用已存在曲目，保留已连接的歌词。导入和备份不自动播放。编排器会在技术校验通过后自动导入，**当前没有阻止导入直到人工批准整曲的强制门禁**；控制台试听后再手动 Play 或开启点歌自动演唱。

明确替换同名旧版本时，制作清单可设置 `replace_titles`；独立离线导入可重复传入 `--replace-title 海屿你` 等标题。只有经过验证的新批次里实际出现的标题允许替换。导入先创建并校验旧歌库完整备份，再发布新文件，将旧音频/guide/元数据移到 `data/songs-replaced/<profile-id>/<transaction>/`，并保存恢复日志；写入失败会尝试回滚。替换需要两份空闲备份配额。新录音不会继承另一版本的 LRC；完全相同版本重复导入则保留已有歌词。禁播策略位于角色设置，替换不解除禁播，也不自动开唱。运行中导入替换还需等待有限演出结束。

明确返回 `stage_busy` / `preparation_busy` 的本地导入等待会继续重试，并反复检查声音及停止标记；其他失败不会无限重试。当前已运行协调器载入的是此前版本，因此本次三首批次另有 `scripts/finalize_song_batch.py` 隐藏收尾任务：它绑定不可变的三首清单，只在原协调器释放锁、全曲导出完成但因旧的 HTTP 409 等待预算失败后接手导入。它不重新制作音频，不越过声音检查，不改变禁播策略，也不启动播放；`batches/20261028-new3/finalizer-state.json` 保存状态。不需重复启动收尾任务。

直接上传路径：**Songs → Upload song MP3 → Audition**。整曲上限 **20 MiB / 10 分钟**。可选 **Upload vocal guide** 上传同起点的人声 MP3，上限相同、时长差最多 2 秒；批处理导入检查更严格，整曲与 guide 时长差最多 0.1 秒。未提供 guide 时按整曲音量估计口型，伴奏可能使嘴张开。

**Backup library** 导出当前角色的录音、guide、元数据和歌词 ZIP，并在 `data/songs-backups/<profile-id>/` 保存。当前容量：每角色 64 条、全库 256 条、音频/guide 合计 1 GiB；每角色最多 8 份备份。备份不包含原始录音、共用 BGM、伴奏、角色档案或密钥，迁移需另外保留。

## 6. 歌词匹配与默认保存

每首歌支持 **Duration · 演唱时长**：默认勾选“完整唱完”。取消后输入原曲开始/结束时间（秒数或 `分:秒`），也可用试听播放器当前位置标记起止点。结束留空表示唱到结尾，区间至少 0.25 秒且不能超出原曲。先“试听区间”，再“保存演唱区间”；区间独立保存在歌曲元数据 `playback_range`，下次手动播放及观众点歌共用。保存不打断当前演唱。原音频和 guide 不剪裁，播放时使用相同偏移并在指定边界结束；歌词仍使用原曲时间，结束后自动回聊天并保留结束语。

优先上传**同一录音版本**的 LRC：**Songs → 选择歌曲 → Upload LRC**。支持 UTF-8、带 BOM 的 UTF-16 及常见中文编码，最大 64 KiB。歌词逐行经过本地审核后写入歌曲元数据；以后每次播放自动加载，重启、暂停、重连不需重新匹配。

- **Lyric offset** 正数延后显示、负数提前显示，范围 -30 至 +30 秒。Apply 只更新歌词，不重新开始音频；Remove lyrics 不删除歌曲音频。
- 歌词由实际播放时钟驱动，不把三秒预加载或转场语算进歌词时间。空白 cue 用于间奏/尾奏清除字幕。
- [LRCLIB](https://lrclib.net/docs) 可提供候选 LRC；标题匹配不能证明录音匹配。项目的 `src/lrclib.py` 是只读查询客户端；当前匹配脚本消费本地 `*-reference.lrc`，不是自动搜索下载歌词的一键入口。
- `prepare_song_lyrics.py` 对本地原始 `vocals.wav` 做离线识别、生成时间锚点；识别文本可能错字或幻觉，不直接作为已确认的正式歌词。保存的源录音与混音哈希、时长必须匹配。

可选识别环境的建立及离线模型首次下载：

```powershell
py -3.12 -m venv .venv-lyrics
.\.venv-lyrics\Scripts\python.exe -m pip install -r requirements-lyrics.txt
.\.venv-lyrics\Scripts\python.exe -X utf8 scripts/download_lyric_model.py
```

把核对过的参考 LRC 存到 `data/lyrics/<BVID>/<歌曲标题>-reference.lrc`。以下识别/匹配命令需 Studio 在本地运行；默认不覆盖已有歌词，也会跳过禁播歌：

```powershell
.\.venv-lyrics\Scripts\python.exe -X utf8 scripts/prepare_song_lyrics.py --title 起风了 --title 小幸运
.\.venv-lyrics\Scripts\python.exe -X utf8 scripts/prepare_song_lyrics.py --title "I Really Want to Stay at Your House" --language en
.\.venv-lyrics\Scripts\python.exe -X utf8 scripts/match_saved_song_lyrics.py
.\.venv-lyrics\Scripts\python.exe -X utf8 scripts/match_saved_song_lyrics.py --apply
```

识别语言默认为 `zh`；英文录音使用 `--language en`，混合语言可用 `--language auto`。语言选项参与缓存签名，避免误用旧语言识别结果。如果人声检测漏掉长音或副歌，使用 `--no-vad` 保留整段录音，重新识别后再核对匹配覆盖率。匹配脚本也支持重复的 `--title` 参数，只处理指定曲目。原始 ASR 是草稿；参考 LRC 必须通过实际录音的时间匹配后才保存为默认歌词。

匹配先尝试固定偏移，必要时允许接近 1 的时间缩放；锚点、覆盖范围或时序不可靠时拒绝导入。`--replace-existing` 只在明确要覆盖手工上传歌词时使用。已完成的 ASR 缓存会复用；当前两首默认歌词已经保存，无需重新识别。

当前校正记录：小幸运整体提前 **3.61 秒**，起风了使用 `参考时间 × 0.974609514 − 0.670302` 秒。保留原繁体歌词，仅将小幸运“他陪我淋的雨”修正为“那陪我淋的雨”。这些是对照识别锚点的自动估计，仍应试听微调，不能标为逐字人工确认。

## 7. 直播播放、点歌与表情

**Pose & face → Scene · 模块场景** 已加入 `singing-room`（星光歌房），Songs 缺省使用它，Chat 缺省继承已保存的银龙座驾。新场景使用透明的持麦 Astra 并复用 DeepSeek/Claude 娃娃，离开歌曲后恢复聊天场景，不改写共用舞台设置。当前 v2 采用休闲游戏风衣装，桌面遮住下半身与空闲手，持麦手和麦克风作为同一刚性组；保留自然呼吸、眨眼与情绪。桌面设备、可见座椅及开心坐姿玩偶已在本地预览检查，几何与音频时钟测试通过；小幅动作连续，音乐律动使用实际音频时钟。可选择 `inherit` 或已有车景作为回退。

控制台选曲并试听，整曲已含伴奏时选择 **暂停背景音乐**，避免重复叠加伴奏。`shared` 仅保留聊天氛围 BGM；独立人声加伴奏使用 `accompaniment`，手工调整人声入点，系统不自动匹配调性、速度或每句歌词。正入点表示伴奏先开始，负数表示人声先开始。**Visual BPM** 只控制站姿的小幅平滑摇头，不改变音频速度。

当前表达使用基础引擎：站姿可有小幅演唱律动；座驾保持身体、手与方向盘的固定几何关系，以对称轻闭眼演唱表情、自然眨眼及真实人声口型表达情绪。不为追求动作数量破坏接触关系或切换到未校准的面部贴图。表情无需额外网络请求，不给固定时长歌曲插入推理等待。

观众可发送 `点歌 起风了`、`点歌《小幸运》`。默认队列最多 10 首，同观众请求间隔 90 秒、同曲冷却 300 秒；歌/故事完成后返回 Chat，至少留 15 秒聊天间隔，再演唱下一首。策略可在当前角色 Songs 中保存。被禁播、尚未完成或不在歌库的歌曲不能排入播放。唱歌期间不插入入场欢迎，避免打断固定时长音轨。

进入与正常结束演出可播放当前声音的缓存转场语，例如“接下来唱一首给你听”和“唱完啦，我唱得怎么样？”。转场未准备好时允许歌曲继续，不把失败的 TTS 变成播放前置条件。Stop 不宣称完整唱完。有限演出结束、停止或故障恢复由共用生命周期释放舞台并回到 Chat，详见基础需求。

## 制作文件与故障定位

| 路径 | 内容 |
|---|---|
| `data/song-sources/` | 原始下载音轨、便携 MP3、`download-results.json`。 |
| `data/voice-references/` | 批准参考音频与描述/哈希元数据；历史试听不代表当前批准声音。 |
| `data/songs-converted/sources.json`、`jobs.json` | 明确的制作输入；不是观众点歌队列。 |
| `data/songs-converted/<BVID>/` | `vocals.wav`、`accompaniment.wav`、`converted-vocals.wav`、`conversion.json`、`aligned-vocals.wav`、`astra-song.mp3`、`converted-vocals.mp3`、`export.json` 及续算缓存。 |
| `data/songs-converted/pipeline-state.json` | 整体状态；`separation-state.json`、`conversion*-state.json`、`separation.log`、`conversion-*.log` 用于细分定位。 |
| `data/songs-converted/prepared.json`、`import-result.json` | 导出清单、入库结果及备份位置。 |
| `data/songs-converted/batches/20261028-new3/` | 本次三首输入快照、激活记录、旧批次清单备份、协调器日志及收尾任务状态。 |
| `data/songs/` | 直播读取的 `<song-id>.mp3`、`<song-id>-guide.mp3`、`<song-id>.json`；非 BVID 文件名。 |
| `data/lyrics/<BVID>/` | 原始 ASR、报告、`*-reference.lrc`、`*-matched.lrc`、`matching-report.json`。 |
| `data/songs-backups/<profile-id>/` | 歌库备份 ZIP。 |
| `data/songs-replaced/<profile-id>/` | 替换后的旧版本与 `replacement.json` 恢复记录，不在活动歌库中。 |

以上音频、模型和制作缓存多数被 Git 忽略，上传代码到 GitHub 不等于备份了歌库。备用配置会使用隔离的歌库目录；歌词自动制作工具当前只覆盖正常配置下已有转换来源的曲目，其他库可用 UI 上传 LRC。

| 现象 | 检查/处理 |
|---|---|
| 源录音不存在或版本不符 | 检查 BVID、原始文件、清单绝对路径及 SHA-256；不要拿同名但不同时长的另一版本顶替。 |
| 声音变化后制作中止 | 先重新试听批准参考并建立对应清单，不关掉一致性检查。 |
| CPU 输出 NaN/尖锐噪声 | 停止使用该结果，检查隔离环境；已验证的 Windows CPU 运行时为 Torch/torchaudio 2.4.1，旧 2.4.0 存在已发现的数值问题。 |
| 内存高、多个 worker 反而更慢 | 减少 worker/线程，保留 50 steps；查看实际并发和各 worker 状态，不把请求的 12 当作实际 12。 |
| 中断、进度长时间不变 | 查看准确的项目子进程、日志和锁持有者，确认原协调器停止后重启同一命令。保留有效 checkpoint，避免重复工作。 |
| 导入 409/声音校验失败 | 等待当前回复/演出结束，检查当前角色和保存声音是否与参考一致；独立离线导入须停止服务。 |
| 有歌但不能点/播放 | 检查当前角色、禁播名单、完成状态、队列容量和冷却。文件存在不意味着曲目允许上台。 |
| 口型随伴奏乱动 | 连接相同起点的纯人声 guide；guide 本身不应作为第二条可听音轨。 |
| 歌词始终早/晚 | 调整 Lyric offset；越唱越偏通常是版本/速度不同，应重做时间缩放匹配，不只改固定偏移。 |
| 没有声音或声音重复 | 检查输出页自动播放解锁、静音、Bilibili/OBS 音频路由及额外预览；只保留一条需要的可听链路。 |

## 验收与后续边界

一次可上台的歌曲应满足：来源版本正确；批准声音与哈希一致；分离/转换全长且音频数值有效；试听无明显漏字、错调、接缝和爆音；整曲/guide 对齐；歌词首段、中段、副歌及末尾均试听通过；暂停、重连、Stop 和正常结束保持同步并返回 Chat；禁播曲目不被点播；入库不自动播放；备份可读且音频哈希不变。

离线验证可使用已有测试（不执行真实下载或整曲转换）：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_separation tests.test_svc_shards tests.test_song_export tests.test_prepared_song_import tests.test_song_bans tests.test_song_requests tests.test_lyric_match tests.test_lyrics -v
npm test
```

尚未实现：通用多角色来源/参考批准向导、一键重建隔离模型环境、强制整曲试听批准门禁、成品快速升降调、乐谱级音高/节拍编辑及自动音乐质量评分。未来实现需复用基础表情和播放生命周期，保持离线制作与直播播放隔离。
