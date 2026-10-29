# Start the controller and HTML preview

Shared feature requirements: [requirement_basic.md](requirement_basic.md). Full song
download, conversion, lyric and playback workflow:
[requirement_song.md](requirement_song.md). Technical design:
[ARCHITECTURE.md](ARCHITECTURE.md).

## Every time you want to use the streamer

1. Open File Explorer and go to `D:\ai auto streamer`.
2. Double-click **Start Studio.cmd**. Keep the console window open. It starts the Python server and opens both pages in your default browser. If the studio is already running, it opens the pages without starting a second server.
3. Use the **controller** at [http://127.0.0.1:8000/control](http://127.0.0.1:8000/control) to edit Identity, Replies, Voice, Actions and Scene. Click **Apply & save** after edits.
4. Use the **HTML preview / output** at [http://127.0.0.1:8000/?overlay=1&controls=0](http://127.0.0.1:8000/?overlay=1&controls=0). If sound is blocked, click inside the page once. Controller thumbnails are silent by default.
5. Keep the server console running while using the pages or Bilibili. Press **Ctrl+C in that console** to stop it. Closing a browser tab does not stop the server.

The HTML pages must be served by Python; do not double-click `frontend/index.html` or `frontend/control.html` as local files. If a page says connection refused, the server is stopped: run the launcher again. If the console says the address is already in use, use the existing server instead of launching another copy.

## Start manually from PowerShell

Open PowerShell and run:

```powershell
cd 'D:\ai auto streamer'
.\.venv\Scripts\python.exe -X utf8 scripts\start_studio.py
```

Or start only the server, then open the two URLs yourself:

```powershell
cd 'D:\ai auto streamer'
.\.venv\Scripts\python.exe -X utf8 main.py
```

Use the project's `.venv` Python; no environment activation or administrator permission is needed. Existing keys in `.env` still work. You can also enter keys through the controller as described below.

## Text and voice API settings

1. Restart Studio after installing this update, then refresh the controller. Open **API connections · 接口设置** above the live metrics.
2. In **Text response**, choose a provider, its Base URL, model and API key. The default is **GLM-4.7-Flash**, with economical fallbacks and thinking explicitly disabled for reasoning-capable GLM models. See **GLM fallback and slow replies** below for the exact order and timing. **Direct** ignores environment/Windows HTTP proxies; it does not bypass a system-wide VPN or TUN.
3. In **Voice / TTS**, choose **MiMo**, **GLM-TTS** or **MiniMax** and enter that provider's key. This selects the credential to edit; select the actual speaking provider in the character's **Voice** settings. GLM-TTS can reuse the saved GLM text key unless you set a separate voice key.
4. Click **Test text** or **Test voice** to check the current draft. These make short provider requests and may incur API charges; the text test follows the configured fallback sequence. They do not broadcast chat or play audio. A successful voice test reports received audio; use the Voice panel's audition to listen.
5. Click **Save API settings**. Changes apply immediately and persist across restarts. Blank key fields preserve existing keys; saved keys are never filled back into the page. Wait for an active reply or voice preparation to finish before saving.

UI settings are shared by all characters and stored locally in `data/.env.api-studio-settings.json`. The file contains private credentials, is excluded by `.gitignore`, and should only be backed up privately. Keys are not stored in character profiles, browser storage, or the public configuration API. Existing `.env` values are fallback credentials; the UI does not rewrite `.env`. Alternate YAML configurations use separate private settings files. These controls are available only from the local controller.

For MiMo, copy the **Base URL and key together from your account's console**. The documented default is `https://api.xiaomimimo.com/v1`; do not guess a regional hostname. MiMo says domestic and overseas account URL/key pairs are not interchangeable. Changing to a China IP and regenerating a key in the same overseas account is therefore not a guaranteed fix. See [MiMo account-region FAQ](https://mimo.mi.com/docs/zh-CN/quick-start/faq/account).

The character's **Voice** panel now defaults to **`mimo-v2.5-tts-voicedesign`**. Describe the voice, timbre, pace and delivery in **Voice description**; this model requires that description and does not use the preset-voice selector. Chat, auditions and stories use the current saved profile description unchanged. The optional **Preset voice** model remains available for comparison. Voice Design does not support singing. Its current streaming interface waits for the whole inference result, so it does not provide early audio even when the request uses `stream: true`. See [MiMo voice-design and streaming instructions](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/speech-synthesis-v2.5).

Voice-design preparation time depends on script length, the provider and network. It is separate from local playback. Stories are prepared ahead of playback; the Stories panel determines readiness using the current script and saved voice, so an older preparation report does not prove that today's recordings are ready.

MiMo's **Auto** route uses an HTTPS/ALL proxy environment variable when configured, otherwise an enabled Windows manual HTTP proxy, otherwise Direct. It does not evaluate PAC scripts. **Environment** retains HTTPX's environment routing. If connection errors occur, compare **Direct** and **Auto** on the same connection. The test reports routing, timing and sanitized error categories (DNS, proxy, timeout, HTTP rejection), without exposing keys. An `APIConnectionError` alone does not establish an account ban. Both providers passed short requests on the US connection; the reported non-US MiMo failure has not yet been reproduced.

## Local interface and live-content protection

After this update, restart the server and refresh the controller/output browser
sources once. Use `localhost`, `127.0.0.1` or `[::1]`; custom DNS hostnames and
remote control are rejected. Private API reads and all writes require the local
controller's `X-Studio-API: 1` header and same origin. WebSockets require the exact
local Origin, including its port. This is a browser-origin boundary, not a login
system for other programs already running on the same computer.

Automatic reading of the viewer's raw chat is now **off by default**, including
the saved default profiles on this PC. If explicitly enabled in Replies, that
prefix is reviewed together with the generated answer. Names, chat and viewer
memory are untrusted user-role data; they never become system instructions.

The model finishes its short answer before review. Only a complete approved
answer can reach subtitles, memory or TTS. This adds the remaining text-generation
time to first speech, but prevents an early safe fragment escaping before a later
unsafe fragment is known. TTS work no longer consumes the model's stream deadline.

Content review runs **locally** as requested, with no additional provider round trip
and no text sent to an external moderation service. It normalizes NFKC, whitespace,
punctuation and invisible characters, then checks local keyword and category rules
for abuse, sexual material, advertising/contact links, dangerous instructions and
selected sensitive topics. Extra words and enabled categories can be edited under
`safety` in `config.yaml`; restart after editing. Common spelling variants are
covered, but local rules cannot reliably understand every homophone or context, and
are not a complete Bilibili content-policy guarantee. Review errors block output.
The optional future reviewer extension also fails closed if explicitly required
but missing; no external service is configured or called by this installation.

Gift/SC intake uses a separate bounded inbox of 128 pending items. Gifts from one
viewer merge over the combo window; one consumer thanks up to eight viewers per
reply. SC questions keep their body and take priority over gifts. Performances
pause this consumer without creating one waiting task per gift. Overload and
failed replies are counted and displayed for manual follow-up; this is best-effort
handling, not a payment-delivery guarantee or a durable payment ledger.

Heartbeat, cleanup, welcome and paid consumers restart after unexpected exceptions.
The controller exposes restart history, current restarts, pending paid items and
overflow/failure counts.

For optional logged-in Bilibili web-protocol reception, put only the SESSDATA value
in `.env` as `BILIBILI_SESSDATA=...`, then restart. The cookie is scoped to
`.bilibili.com`; it is not included in public config or logs. Logged-in reception
does not guarantee that Bilibili exposes UID or every paid event. Redacted UID=0
uses a temporary nickname-derived identity and has no personal-memory record.
Identical/redacted nicknames still cannot be distinguished reliably. The official
Open Live integration remains a separate future integration requiring its own
application credentials and session lifecycle.

## Character modules and their poses

Restart Studio and refresh the controller and output once. **Character modules** contains **Chat**, **Songs**, and **Stories**. Each card has **Pose & face · 姿态表情**, including **Scene · 模块场景**. These settings belong to the selected character and module: Astra can have one chat look, a different story look and a singing look. New characters receive independent copies. Scene assets, DeepSeek/Claude dolls, framing and background music remain shared; `scene_preset` selects a temporary module backdrop without rewriting the saved shared stage. Chat normally inherits the saved car scene. The car artwork keeps its fixed driving contacts; standing artwork uses the supported joint poses.

Every finite performance returns to **Chat** when its audio ends or **Stop** is pressed. Chat messages remain subject to the existing bounded queue and overload policy while a performance owns the stage.

## Stories · AI 合租日记

Open **Stories · 故事**. The library contains ten original, fictional, funny and cute everyday-life stories featuring only **Astra, Claude and DeepSeek** as human-like housemates:

1. 大小姐早餐厅，暂不赊账
2. 蛋糕侦探与奶油证据
3. 大扫除标签贴到了谁身上
4. 停电夜的银龙影子剧场
5. 泡泡很多，威严很少
6. 凌晨泡面，煎蛋归谁
7. 薄荷盆里长出了值班表
8. 开播排练与神秘小抄
9. 雨天出发前的行李检查
10. 晚安大会，谁先打哈欠

Prepared recordings are stored in `data/stories/<character-id>/`. Check the Stories panel for current readiness and duration: changes to the script or saved voice make older recordings ineligible. Stories pass the same saved voice description used by chat directly as the user message, without adding an adult role, a different speaker or scene directions. Inline acting cues such as breath, quiet speech and a soft laugh remain in the assistant's story text. Replaying a matching prepared story needs no live conversation-model request.

- **Random story · 随机讲一个** chooses a prepared story. One shuffled round uses each ready story once, and the next round avoids immediately repeating the last one.
- **Tell selected · 讲这个** plays the chosen story. Use the audio player to audition it first.
- **Prepare ten · 准备十个故事** generates only missing recordings for the selected character and current voice description. Changing the voice description changes which recordings are considered current. Preparation makes real MiMo requests; audition and stage replay use local files.
- **Stop · 回到聊天** ends the story and returns to chat. Closing the settings dialog stops only its audition, not the stage performance.

Under **Triggers & interruptions**, viewer requests are enabled by default. Supported messages include **“讲个故事”**, **“来个故事”**, **“换个故事”** and **“停止故事”**. Play/change requests have a **45-second** default cooldown. Asking for a story while one is already playing does not restart it. **“继续故事”** is accepted while a story is active; welcome interruptions resume automatically.

The optional **quiet-room story trigger** starts disabled. If enabled and saved, its defaults are **120 seconds without viewer activity** and at least **600 seconds between automatic stories**. It uses prepared audio only. Regular warm-up lines do not reset the viewer-quiet timer. These trigger settings belong to the current character.

### Welcoming viewers during a story

Arrival events use a separate welcome collector, so they can be handled while ordinary chat is paused. Default behavior collects arrivals for **5 seconds**, selects up to **3 names**, groups any remaining arrivals in the same welcome, and waits at least **15 seconds** between welcome batches. The same viewer is not repeatedly named within **300 seconds**.

Astra prepares the welcome audio while the story continues. When that audio is ready, the output saves its current story position, pauses, welcomes the arrivals, and resumes the unfinished story from that position. The story is not regenerated or restarted. Singing is the exception: arrivals during a song are suppressed instead of being saved for a later interruption. The normal chat module also welcomes arrival batches.

To prepare the ten stories without connecting to Bilibili or broadcasting:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\prepare_astra_stories.py
```

## Songs · uploaded performances

Open **Songs · 歌曲**, then **Upload song MP3 · 上传整曲**. Upload the specific recording you want to use, or a version already converted into Astra's voice. Full songs may be up to **20 MB / 10 minutes**. Uploading stores a local copy in the selected character's song library; stage playback does not call TTS or alter its melody.

1. Select and audition the uploaded recording.
2. Optionally use **Upload vocal guide · 上传人声口型轨** for a vocal-only MP3 stem. It must begin at the same point as the full song, be at most 20 MB, and differ in duration by no more than **2 seconds**. The guide is silent in the output and drives the mouth analyser; the full recording remains the audible track.
3. Without a vocal guide, mouth movement approximates the full mix's amplitude, so accompaniment can also open the mouth. A vocal-only guide gives a cleaner result.
4. For a complete mixed recording, choose **Quiet / 暂停背景音乐**. **Keep shared background music** retains the current file/system music; **Uploaded accompaniment** is intended for a separate vocal take and backing track. Its optional entry offset aligns starts only, not tempo or pitch.
5. Click **Play on stage**. Chat replies and warm-up lines pause, audience welcomes do not interrupt, and the end of the recording returns to Chat. If audio is blocked, click the output once.

The stage uses the module's selected expression, blinking and mouth animation. The standing rig adds a small smooth head sway; **Visual BPM** changes that visual movement only. The painted car pose retains its fixed head/body/hand geometry. Songs with an attached LRC display synchronized lyrics using the recording's playback clock; songs without one show the title. See **Lyrics, temporary song bans, and the new stories** below.

The earlier generated mini-songs and test takes remain available through **Show old experimental tracks · 显示旧的实验生成曲目**, which starts unchecked. Their pitch/rhythm quality did not meet the requested result, so uploaded recordings are the primary path. The collapsed experimental TTS section uses the separate preset `mimo-v2.5-tts` endpoint, because Voice Design excludes singing. Its earlier long-form test suggested skipped lyrics; this feature does not promise accurate covers or a particular melody.

**Backup library · 备份歌库 ZIP** exports the selected character's recordings, metadata, lyrics when available, and any attached vocal guides. A local copy is kept in `data/songs-backups/<character-id>/`; the ZIP includes SHA-256 checksums and restore instructions. API keys, identity settings, shared BGM and accompaniment files are not included. The library holds 64 takes per character (256 total), up to 1 GB of audio/guide files, and up to eight backup ZIPs per character. Keep those other project assets separately when moving the entire setup.

## Offline conversion into Astra's voice

The downloaded Bilibili recordings remain in `data/song-sources/`; their URLs and local files are recorded in `download-results.json`. The following five recordings have completed conversion, export and import with **50 diffusion steps**:

| Song | Source | Current playback policy |
|---|---|---|
| 起风了 · 买辣椒也用券 | [BV1D64y1Z7Ko](https://www.bilibili.com/video/BV1D64y1Z7Ko/) | Allowed; default lyrics saved |
| 小幸运 | [BV1y82uB2ET4](https://www.bilibili.com/video/BV1y82uB2ET4/) | Allowed; default lyrics saved |
| 告白气球 | [BV1A3411J7HX](https://www.bilibili.com/video/BV1A3411J7HX/) | Temporarily blocked |
| 大鱼 | [BV1f59QBGEhx](https://www.bilibili.com/video/BV1f59QBGEhx/) | Temporarily blocked |
| 海屿你 | [BV14nrkBPEBE](https://www.bilibili.com/video/BV14nrkBPEBE/) | Temporarily blocked |

稻香、七里香、平凡之路、夜空中最亮的星 and 牵丝戏 remain outside the completed conversion batch. Its **5/5 complete** metadata was archived under `data/songs-converted/batches/20261028-new3/previous-completed-batch/`. No conversion or playback needs to be started to use the existing allowed songs. The three blocked recordings remain on disk. The current preparation script does not filter bans by itself, so new preparation manifests must contain only the intended work. See [requirement_song.md](requirement_song.md) before adding recordings or changing the approved voice.

**New batch, 2026-10-28:** I Really Want to Stay at Your House (`BV1FN411n7FT`), a new 海屿你 recording (`BV14GovBnESm`), and 泡沫 (`BV1y8411i7Dd`) have been downloaded and started with the same approved voice, **50 steps / three conversion workers**, bounded by available RAM. Each worker waits for its separated vocal stem before converting. The current `jobs.json`, `sources.json` and `pipeline-state.json` now describe these three recordings only; the previous five songs are not restarted. New 海屿你 will replace the old same-title library version only after validated conversion and a verified pre-replacement backup. Its ban remains in force. The new songs are still being prepared, and no playback starts automatically.

The singing target is **Astra's current saved Voice description**, also used unchanged for chat and stories. The approved reference is `data/voice-references/astra-current-voice.wav`: **18.936 seconds**, mono 24 kHz PCM16, with approval, profile description and SHA-256 recorded beside it in `astra-current-voice.json`. Conversion uses the first eight seconds of this same reference. The earlier B auditions and reference are historical alternatives. Changing the saved voice or approved reference stops preparation to avoid publishing songs with the wrong voice.

The pipeline separates vocals from the original recording, converts only those vocals with [Seed-VC](https://github.com/Plachtaa/seed-vc), and remixes them with the accompaniment. It preserves source pitch and duration with F0 conditioning, automatic F0 adjustment off, length adjustment 1.0 and zero semitone shift. The separate converted-vocal file drives lip sync silently. Stage playback uses local files.

For a future unfinished batch with validated source/voice manifests, start or resume manually in a separate PowerShell console and keep it open. **Do not rerun this merely to play the five already completed songs.**

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\prepare_astra_songs.py --steps 50 --workers 1 --threads-per-worker 6
```

Use another console to inspect progress or request a stop. Running the start command again resumes validated completed work:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_astra_songs.py --status
.\.venv\Scripts\python.exe scripts\prepare_astra_songs.py --stop
```

The controller's **Songs → Song preparation · 歌曲准备** panel shows completed count, current stage/title and an estimate when available. A complete validated batch is imported with its mixes and vocal guides and backed up. Import does not start playback. Keep Studio available for the local import API; the independent offline import script requires Studio to be stopped.

This PC has Intel UHD Graphics 770 and no NVIDIA GPU. The configured run uses **one converter with six CPU threads**, alongside **one separator with four threads**. The supported ceiling is 12 requested conversion workers, further bounded by track count and available RAM. A [two-worker/four-thread test](data/qa/profiling/seed-vc-parallel-evaluation.json) had about **11.4% lower throughput** than one six-thread converter on this PC; worker count leaves the 50-step quality settings unchanged. The model environment is isolated in `.venv-svc`, using the verified **Torch/torchaudio 2.4.1 CPU** fix; the live application's `.venv` was not changed. Numerical and excerpt checks are recorded in [demucs-setup.json](data/qa/demucs-setup.json), [seed-vc-setup.json](data/qa/seed-vc-setup.json) and [cpu-math-torch241.json](data/qa/cpu-math-torch241.json).

`scripts/convert_song_voice.py` remains a low-level single-source wrapper; the preparation script above manages the selected batch, resume checks, exports and safe import. Prepared story files, voice references, the song library, converted audio and private API settings stay outside Git. Keep the original recordings with your own source backups.

## Put the HTML output into Bilibili

1. Open 哔哩哔哩直播姬.
2. Set **直播设置 → 视频 → 横屏4:3** and output **1440 × 1080**.
3. Add or edit a **浏览器** source with URL `http://127.0.0.1:8000/?overlay=1&controls=0`. Set the browser source to **1440 × 1080**, then fit and center it on the canvas.
4. Keep one audible route into Bilibili. Mute extra browser/controller previews if you hear doubled voices or music. Test a room message and check Bilibili's audio meter.
5. Room **22101382** is the configured chat input. Starting this Python application connects chat; starting a public broadcast remains a separate action in Bilibili.

When frontend code changes, refresh the Bilibili browser source once. Ordinary saved controller settings update open outputs immediately.

## Defaults saved on 2026-10-26

The 2026-10-26 settings snapshot was copied into `config.yaml` and frozen in `data/studio-defaults.json`. Current character profiles remain authoritative for later edits, including the current voice. The setup includes:

- Bilibili room **22101382**. The current text default is **GLM-4.7-Flash** with economical fallbacks and thinking off; the frozen 2026-10-26 snapshot predates these changes. The frozen preset originally used **MiMo 冰糖**; the current Astra profile selects **MiMo Voice Design** with the current saved description, shared by chat, stories and the approved song reference.
- Car scene by default, a slow looping countryside view, gentle camera vibration, companions and bottom blur; **4:3 / 1440 × 1080** output.
- Subtitles synchronized to voice, held **2 seconds** after playback.
- Background music enabled at **22%**, automatically quieter during speech.
- Warm-up enabled after **15 seconds** of quiet, at least **30 seconds** between lines. These are your latest saved values; adjust them under **Actions**.

`data/character-profiles.json` now saves every character and the shared stage. Existing values were migrated into **Astra**, which is selected on every server startup. `data/studio-settings.json` is a compatibility export; editing it alone no longer overrides the profile library. Back up the entire library to preserve all characters. To restore a backup, stop the server and restore that library before restarting. The frozen pre-profile preset remains at `data/studio-defaults.json`; the previous YAML is at `data/backups/config-before-saved-defaults.yaml`.

## Switching and adding characters

1. Open the controller. The **Current character / 当前角色** dropdown at the top switches profiles immediately. Astra has the **STARTUP DEFAULT** badge.
2. Choose **＋ New character**, enter a name, then **Create & switch**. The new profile starts as a copy of the current one; edit its **Identity**, **Replies**, **Voice**, and **Actions**, then **Apply & save**.
3. In **Actions**, edit the character's warm-up lines, one sentence per line. Voices can have separate providers, preset voices and voice directions. API credentials are shared by provider; edit them in **API connections** or `.env`.
4. **Scene & music** controls the shared stage: car/background, driving effects, DeepSeek and Claude, framing, subtitles, and background music. Switching profiles preserves these settings and keeps the same output URL.

Save or discard drafts before switching. If a reply is being prepared, wait for it to finish and select again. Once switched, any remaining old-character speech in the browser queue is stopped. The next reply uses the new identity and voice. Every restart returns to Astra while keeping other profiles saved.

All profiles currently use the existing **Astra artwork**. Independent visual characters need new artwork packs; the current car illustration has Astra and both dolls painted into the same composition.

After installing this update, start **Start Studio.cmd** (or restart it if already running), refresh the controller, and refresh the Bilibili browser source once.

## Upload your own background or music

Open **Scene & music**:

- **Upload PNG** adds a local image to the **Backdrop** list and previews it behind the standing character and dolls. PNG files can be up to **20 MB**, at most **8192 pixels** on either side and **25 megapixels** overall. The original resolution is retained; the image fills the stage with centered cropping when its aspect ratio differs.
- **Upload MP3** adds a local track to **Music track**. MP3 files can be up to **50 MB**. Use the audio player to audition it, then adjust the volume. Live output loops the chosen track and automatically lowers it during speech.
- Click **Apply & save** to use the selection on the live output. Uploading alone does not change the live scene. Select a built-in backdrop or **Conversation lounge** to switch back.

Uploads stay on this computer in `data/media-library/`, shared across all character profiles and available after restarting. Keep that folder together with `data/character-profiles.json` when backing up or moving the project. The library holds up to 200 files / 1 GB; alternate test configurations use separate folders. Uploaded PNGs are full stage backgrounds; they do not replace the car's animated window layer.

## Capture music from a tab or app

1. Restart Studio after this update and refresh the Bilibili browser source once. Open `http://127.0.0.1:8000/control` in current **Chrome or Edge**. Embedded browsers may not provide the audio picker.
2. In **Scene & music → Music source**, choose **System sound · 采集标签页 / 应用**. Click **Choose tab / app · 选择声音**. Select a music tab and enable **Share tab audio**. An app window works only if the browser offers its separate audio; if there is no audio checkbox, use the music service's browser tab instead.
3. Click **Apply & save**, and keep this controller tab open. Audio travels directly to the full stream output using local WebRTC. Preview thumbnails stay silent. The existing **Music volume** slider applies, with a 75% reduction while the character speaks. Switching characters keeps the capture session.
4. **Stop** ends capture immediately; the shared-stage card also has a stop button when the dialog is closed. Refreshing/closing the controller or using the browser's Stop sharing ends capture. The source setting persists, but permission cannot be restored automatically: choose the music tab/app again each session.

Select only the music source, never the stream output or Bilibili app. Whole-desktop audio is intentionally excluded to prevent voice feedback. Tab capture requests suppression of its original local playback; if the browser or app still plays that original audio, exclude it from Bilibili's other mixer inputs so it is not recorded twice. Volume/ducking affect the project's received copy only. Keep one audible output page; mute extra full previews. If an output browser blocks autoplay, interact with it once. Local WebRTC requires browser support and local connectivity; there is no cloud relay or recording. Selecting **Music file** restores the saved built-in/uploaded track.

Browser capture capabilities: [Chrome screen-sharing controls](https://developer.chrome.com/docs/web-platform/screen-sharing-controls), [getDisplayMedia API](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia).

## First-time dependency installation on another machine

Install Python 3.12, copy the project, then run these commands from its folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run **Start Studio.cmd**, open **API connections**, and enter your text and voice credentials. Alternatively, create `.env` from `.env.example` only if `.env` does not already exist, and enter your own `GLM_API_KEY` and `MIMO_API_KEY` there before starting. Node.js is needed for frontend development tests or rebuilding the optional VRM bundle; the bundled 2D output runs without it.

## New scene: Astra's car

The car scene now opens by default. In **Scene**, use **Slow scenery loop**
and **Gentle camera vibration** to switch the driving effects on or off, then
click **Apply & save**. The preview has eight expression buttons and a blink
button. The seated pose stays fixed; real speech drives the mouth. The existing
1440 × 1080 browser-source size and output URL work unchanged. Refresh an
already-open Bilibili browser source after loading this update.


Local expression blending, symmetric eye controls, blinking and real-audio mouth
movement are implemented below. Further artwork calibration and large new poses
still need compatible assets and review; an older pending-task entry does not
mean that all expression features remain postponed.

## Songs scene: Night music room

**Pose & face → Scene · 模块场景** now includes **Night music room · 星光歌房**.
Songs defaults to `scene_preset: singing-room`; other modules can inherit the
saved shared stage. This temporarily selects the song scene and restores Chat's
shared car scene after the song, rather than replacing the saved scene setting.

The scene uses Astra's consistent silver hair, violet eyes, horns and ornament
with an ivory knit top and lilac gaming hoodie. She holds a microphone; her other
hand stays below the continuous tabletop and is invisible. The visible chair,
gaming/music equipment and happy seated DeepSeek/Claude dolls complete the room.
Breathing and blinking continue locally at idle; restrained head/mic movement
follows song position, and lips follow actual voice audio. The gripping hand and
microphone move together, with no pose-image switching or extra provider request.

Open the [muted scene preview](http://127.0.0.1:8000/?preview=1&overlay=1&muted=1&scene=singing-room).
In **Scene**, choose **Night music room** to preview expressions with the local
buttons; only **Apply & save** changes the shared scene. Module scene selection
normally switches automatically for Songs and restores Chat afterward. Local
rendering/geometry and audio-clock integration tests pass; the painted rig remains
limited to small calibrated movements, rather than arbitrary full-body gestures.

## Local expression prototype (2026-10-27)

The local prototype adds continuous expression blending to the existing Astra
artwork. After updating, restart Studio and refresh the controller and the
Bilibili browser source once. No new API key or local AI model is needed.

1. In the controller's module shelf, open **Pose & face · 姿态表情** for **Chat**,
   **Stories** or **Songs**.
2. **Local expressions · 本地情绪表情** enables that module's emotional expression.
   **Expression strength · 表情幅度** adjusts it from 0–100%; the default is 55%.
   The separate **Movement** slider still controls body movement.
3. Click **Save module look · 保存模块表演**. Draft changes do not affect the live
   output. Each character and module keeps its own preferences. Creating a
   character copies the current preferences; later changes stay independent.

Turning expressions off keeps a neutral face while natural blinking and
audio-driven mouth movement continue. Expression strength does not amplify
the mouth or blink. The car retains its painted seated pose, hand position and
steering-wheel contact. Backgrounds, DeepSeek/Claude dolls and music remain shared.

The prototype uses the published **`@soullink-emotion/engine@0.1.0-beta.1`**, whose
installed package and bundled license are **MIT**. It uses local animation only;
Embedding and JEV/LLM action planning are not enabled, so expressions add no API
requests. The current upstream development branch has a different version and
license; the pinned installed package is the one used here. This update adapts
the existing Canvas artwork and does not create a `.moc3` Live2D model or new rig.
Further facial artwork calibration can still be reviewed separately.

Singing uses the recorded audio timeline, with the existing 3-second preload lead
and 80 ms Web Audio scheduling cushion. Expression updates add no preparation
request or startup wait. The singing expression holds until the recording ends;
beat sway starts only with audible scheduled audio, pauses on mute/buffering, and
resumes at the correct position. Finishing returns directly to Chat.

## Song requests and spoken transitions

Restart Studio and refresh the controller and your output browser source once.
Open **Modules → Songs → Song requests · 点歌** to enable chat requests, pause
automatic playback, adjust the chat break, or manage the waiting list.

Viewers can send `点歌 起风了`, `点歌《小幸运》`, or `/song 大鱼`.
Requests match completed songs in the selected character's library. Songs still
being converted become requestable after import. No model, download, or audio
generation is started by a viewer request.

The default queue holds 10 songs, with 90 seconds between a viewer's requests
and five minutes before repeating a recently played song. The current song or
story finishes first. Between songs, Chat resumes for at least 15 seconds; this
break is adjustable. Automatic playback requires a connected output page;
controller and silent preview tabs do not consume the queue. The waiting list
is temporary and clears when Studio restarts; its policy is saved per character.

Songs and stories have three varied introductions and three natural endings,
spoken in the selected character's voice. For example: “那我唱首歌给你听，好不好？”
and “唱完啦，我唱得怎么样？” A manual stop does not claim that a song finished.
The short lines are cached locally by voice settings; replaying a cached line
makes no TTS or LLM request. The intro plays while the song preloads, and the
recording keeps its original duration, pitch, and audio clock.

To prepare all twelve transition clips ahead of time without playing anything:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/prime_module_transitions.py
```

Changing voice settings creates new cached takes. A failed or slow transition
request does not prevent the saved song or story from playing.

## Lyrics, temporary song bans, and the new stories

In **Songs**, select a song and use **Upload LRC** to attach its matching timed
lyrics. UTF-8, UTF-16 with a BOM, and common Chinese encodings are supported;
the file must be at most 64 KiB. Lyrics appear on the output using the same
clock as the recording. Late joins, pauses, and resumes keep their position.

Use **Lyric offset** to correct timing without restarting audio: a positive
number displays lines later; a negative number displays them earlier. Click
**Apply** to update the live output. **Remove lyrics** removes only the attached
lyric data. The original MP3 and the LRC file on your computer remain intact.
Use an LRC timed for the same recording edition; this feature does not fetch
lyrics or transcribe audio automatically.

**Temporarily blocked songs** is saved per character. 告白气球、海屿你 and 大鱼 are
currently blocked for Astra, including chat requests and manual stage playback.
Their audio files remain available locally; remove a title from the list and
save when you want to allow it again.

The ten current stories feature only Astra, Claude and DeepSeek. Prepared audio
uses the saved Astra voice. With the story's face set to **Auto**, gentle moods
follow the script's acting cues on the playback timeline. These facial cue times
are estimated from spoken text; lip movement uses the actual audio envelope.

### Saved lyric defaults (2026-10-28)

起风了 and 小幸运 use the supplied reference lyrics, retimed against their local
vocal recordings. 小幸运 starts 3.61 seconds earlier than its reference LRC.
起风了 uses `reference time × 0.974609514 − 0.670302` seconds because the saved
recording runs slightly faster. These are automatic alignment estimates; adjust
**Lyric offset** after listening if needed. The supplied Traditional Chinese text
is preserved, with 小幸运's “他陪我淋的雨” corrected to “那陪我淋的雨”.

Corrected LRC files and timing reports are in `data/lyrics/BV1D64y1Z7Ko/` and
`data/lyrics/BV1y82uB2ET4/`. The `*-reference.lrc` files preserve the originals;
`*-matched.lrc` files contain the adjusted times. Saved lyrics load automatically
whenever these songs play and add no provider request to playback. The three
blocked songs are skipped.

To check cached reference matching without recognition or remote requests:

```powershell
.\.venv-lyrics\Scripts\python.exe -X utf8 scripts/match_saved_song_lyrics.py
```

Add `--apply` to save accepted matches through the local Studio API while it is
running. Existing uploaded lyrics are preserved unless `--replace-existing` is
explicitly supplied. The isolated `.venv-lyrics` environment uses
`requirements-lyrics.txt`; normal Studio playback does not need it.

## GLM fallback and slow replies (2026-10-28)

Open **API connections → Text** to edit the primary model and the fallback
list. **Use economical order** sets this order:

`glm-4.7-flash → glm-4-flash-250414 → glm-4-flashx-250414 → glm-4-air-250414 → glm-4.5-air → glm-4.7-flashx`

The two first choices are free. FlashX-250414 costs ¥0.1 input / ¥0.1 output
per million tokens; Air-250414 is ¥0.5 / ¥0.5; 4.7-FlashX is ¥0.5 / ¥3.
4.5-Air's short-context, short-output tier is ¥0.8 / ¥2. The busy 4.7-FlashX
is deliberately placed last. These are the prices checked on 2026-10-28 at
[BigModel's official pricing page](https://docs.bigmodel.cn/cn/guide/start/pricing),
which also retains prices for historical models. Actual model access depends
on the account. An unavailable model is skipped after its error.

The published GLM-4.7-Flash model is 30B total / 3B active parameters; GLM-4.5-Air
is 106B / 12B. Other commercial sizes in the controller are marked unpublished.
Sources: [GLM-4.7-Flash model card](https://huggingface.co/zai-org/GLM-4.7-Flash),
[GLM-4.5 documentation](https://docs.bigmodel.cn/cn/guide/models/text/glm-4.5).
Model size alone does not predict response latency on a shared API service.

Temporary service errors, rate limits, empty replies and timeouts move to the
next model. Defaults allow three seconds for the first usable text per model
and twenty seconds for the entire reply. A failed model cools down for ninety
seconds and is skipped on subsequent replies; it becomes eligible again afterward.
Every model is tried at most once in a reply. Authentication, certificate and
content-review failures stop the request. Once text has been yielded, a failed
stream is not spliced together with another model's reply.

All selected reasoning-capable GLM models receive `thinking.type: disabled`.
Older plain GLM-4 models have no thinking mode. Thinking parameters are never
removed to retry a GLM request. GLM-4.5-Flash is excluded because the official
docs say it redirects to GLM-4.7-Flash. Models requiring thinking are excluded.
See the [thinking-mode documentation](https://docs.bigmodel.cn/cn/guide/capabilities/thinking-mode).

If a live reply is still incomplete after three seconds, Astra can say:
“不好意思，我的思考模型正在检修中，稍等一下下～”. The fixed line uses the
current character's voice, with lip sync, matching subtitles, and normal music
ducking. It plays once per wait, at most once every forty-five seconds. Fast
replies cancel it, including any unfinished preparation. No notice plays during
a song/story or when no output page is connected. Use the Text panel checkbox
to disable it. This line is a character interaction, not a provider status report.

To cache that line without playing it:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/prime_module_transitions.py --wait-only
```

Restart Studio and refresh the controller and Bilibili browser source once
after updating. The Text connection test now follows the live streaming mode
and reports the successful model, first-text latency, and models attempted.
