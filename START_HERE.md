# Start the controller and HTML preview

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

Use the project's `.venv` Python; no environment activation or administrator permission is needed. Dependencies and API keys are already configured on this computer. API keys remain in `.env`.

## Put the HTML output into Bilibili

1. Open 哔哩哔哩直播姬.
2. Set **直播设置 → 视频 → 横屏4:3** and output **1440 × 1080**.
3. Add or edit a **浏览器** source with URL `http://127.0.0.1:8000/?overlay=1&controls=0`. Set the browser source to **1440 × 1080**, then fit and center it on the canvas.
4. Keep one audible route into Bilibili. Mute extra browser/controller previews if you hear doubled voices or music. Test a room message and check Bilibili's audio meter.
5. Room **22101382** is the configured chat input. Starting this Python application connects chat; starting a public broadcast remains a separate action in Bilibili.

When frontend code changes, refresh the Bilibili browser source once. Ordinary saved controller settings update open outputs immediately.

## Defaults saved on 2026-10-26

The latest saved settings were copied into `config.yaml` and frozen in `data/studio-defaults.json`. They include all identity, reply, voice, action and scene controls:

- Bilibili room **22101382**, GLM Flash replies, **MiMo 冰糖** with your current voice direction.
- Car scene by default, a slow looping countryside view, gentle camera vibration, companions and bottom blur; **4:3 / 1440 × 1080** output.
- Subtitles synchronized to voice, held **2 seconds** after playback.
- Background music enabled at **22%**, automatically quieter during speech.
- Warm-up enabled after **15 seconds** of quiet, at least **30 seconds** between lines. These are your latest saved values; adjust them under **Actions**.

`data/character-profiles.json` now saves every character and the shared stage. Existing values were migrated into **Astra**, which is selected on every server startup. `data/studio-settings.json` is a compatibility export; editing it alone no longer overrides the profile library. Back up the entire library to preserve all characters. To restore a backup, stop the server and restore that library before restarting. The frozen pre-profile preset remains at `data/studio-defaults.json`; the previous YAML is at `data/backups/config-before-saved-defaults.yaml`.

## Switching and adding characters

1. Open the controller. The **Current character / 当前角色** dropdown at the top switches profiles immediately. Astra has the **STARTUP DEFAULT** badge.
2. Choose **＋ New character**, enter a name, then **Create & switch**. The new profile starts as a copy of the current one; edit its **Identity**, **Replies**, **Voice**, and **Actions**, then **Apply & save**.
3. In **Actions**, edit the character's warm-up lines, one sentence per line. Voices can have separate providers, preset voices and voice directions. API keys remain in the project's `.env` and are shared by the corresponding provider.
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

## First-time dependency installation on another machine

Install Python 3.12, copy the project, then run these commands from its folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create `.env` from `.env.example` only if `.env` does not already exist, and enter your own `GLM_API_KEY` and `MIMO_API_KEY` there. Then run **Start Studio.cmd**. Node.js is needed for frontend development tests or rebuilding the optional VRM bundle; the bundled 2D output runs without it.

## New scene: Astra's car

The car scene now opens by default. In **Scene**, use **Slow scenery loop**
and **Gentle camera vibration** to switch the driving effects on or off, then
click **Apply & save**. The preview has eight expression buttons and a blink
button. The seated pose stays fixed; real speech drives the mouth. The existing
1440 × 1080 browser-source size and output URL work unchanged. Refresh an
already-open Bilibili browser source after loading this update.


Facial-expression cleanup (including unequal eye sizes) is saved for a later
session in `data/pending-tasks.json`, as requested on 2026-10-27. It is not
scheduled automatically.
