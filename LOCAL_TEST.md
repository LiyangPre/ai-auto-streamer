# Run the local demo (Windows PowerShell)

The dependencies are already installed in the project-local `.venv`. Use `.\.venv\Scripts\python.exe` below, or activate it with `.\.venv\Scripts\Activate.ps1` before using `python`. This environment contains the maintained, pinned Bilibili client; the system Python still has the old PyPI package. You do not need an API key for offline tests.

## 1. Start the fully offline demo

```powershell
cd 'D:\ai auto streamer'
.\.venv\Scripts\python.exe main.py --config config.local-test.yaml
```

Leave that terminal running. Open **http://127.0.0.1:8001/control**.

This profile uses mock replies, no TTS, a 0.3-second collection window, no reply cooldown, and the reference-based silver dragon sprite. Port 8001 lets it run alongside the normal controller on port 8000. If the test server is already running, open the URL instead of launching a second instance.

Try the following:

1. Click **你好呀**. The activity feed should show your message and a reply.
2. Type **你是谁呀** and press Enter. You should see Lunaria introduce herself.
3. Click **点头**, **思考**, **害羞**, **轻笑**, and **介绍**. Arms stay low, feet and dress remain fixed, and interrupted gestures blend smoothly. Only six restrained gestures are exposed; old raised-arm poses are removed. The face blinks automatically.
4. Switch **2D · Lite** / **3D · VRM** to compare renderers. The switch changes only the preview.
5. This offline profile is intentionally silent. For online Edge-TTS voice, use the normal `python main.py` configuration on port 8000, then enable preview sound.

Press **Ctrl+C** in the server terminal to stop it.

## 2. Verify the message path in a second terminal

```powershell
cd 'D:\ai auto streamer'
.\.venv\Scripts\python.exe test_integration.py --base-url http://127.0.0.1:8001
```

Expected final line:

```text
PASS: HTTP input -> WebSocket chat -> reply
```

## 3. Check facial animation and real voice

Start the normal server with `python main.py`, then open **http://127.0.0.1:8000/rig-preview**.

1. Click **Close-up** and choose a facial expression. Eyes should blink at irregular intervals; use **Blink** for an immediate preview.
2. Click **Test current voice**. This uses the configured TTS voice, plays locally, and does not send a chat message or broadcast to the controller.
3. Watch the mouth and the mouth-opening meter. The lips should follow syllable loudness, close between phrases, and remain closed after playback ends. **Stop** also cancels a pending preview.
4. Return to the controller, enable preview sound, and send a message to check the same mouth animation with a live reply.

The offline profile has no speech engine; use port 8000 for the voice test. Facial animation and gestures themselves work offline. This is lightweight audio-reactive lip sync, not phoneme recognition.

## 4. Test scenery, framing and fullscreen

1. Open the controller and click **Scene**. Choose **Royal palace**, **Rose garden**, or **Moonlit terrace**. Palace selects gentle golden motes; the outdoor scenes select clouds. You can also choose rain or still mode.
2. Toggle **Cute companions** and **Bottom blur**, then adjust bottom softness. The two chibi companions are static bowed images. The bottom blur fades upward and leaves the face clear.
3. Drag inside the preview, or focus it and use the arrow keys (Shift for larger steps). Test the size slider, then **Reset position & size**. These changes affect only the draft preview until saved.
4. Click **Apply & save**, then **Open full scene**. Changes appear in open outputs immediately and survive restarting the server. Move the pointer to the upper right and click **Fullscreen**; click **Exit fullscreen** or press Escape to return.
5. Select **Transparent** to hide scenery, atmosphere, companions and blur. Use **Discard edits** to return to saved settings.

For OBS use `http://127.0.0.1:8000/?overlay=1&controls=0` at 1440 × 1080 (4:3) (replace the port with 8001 for the offline profile). Add `&transparent=1` for a transparent character-only source regardless of saved settings. Background artwork is bundled locally; the visual test needs no API key or network service.

## 5. Run automated regressions

```powershell
cd 'D:\ai auto streamer'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm test
```

These tests use local mocks and do not call a paid model. The Node tests cover ordered audio, cancellation, autoplay handling, bounded playback, gesture completion, frame-rate consistency, reduced-motion behavior, missing expression artwork, joint limits, fixed feet, blink timing, expression blending, audio noise gating, fast lip attack, silence release, buffering events, bounded scene effects, transparent output and stable scaling. Python tests also verify scene validation, persistence and WebSocket updates.

Only needed after changing the VRM source:

```powershell
npm run build:vrm
```

## 6. Test Bilibili chat and gifts without going live

```powershell
cd 'D:\ai auto streamer'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_bilibili.py -v
```

This starts an isolated local test client, replays community web-protocol packets (including V2 Protobuf gifts), and exercises chat/gift/Super Chat/membership/entry input through local WebSocket replies. It uses mock replies and no voice/network service, and does not inject events into your running studio. Entry welcome replies are enabled only in this test; production defaults leave them off.

Official Open Live credentials and a real room are not needed for these offline checks. The community web-protocol receiver can also be tested separately against a real room:

```powershell
.\.venv\Scripts\python.exe scripts/test_bilibili_live.py 22101382 --seconds 120
```

This bounded, read-only probe prints public room status, successful WebSocket authentication, and received chat/gift events. It does not start broadcasting, post chat, buy gifts, invoke LLM/TTS, or change the running studio's input mode. Send a message yourself in the Bilibili room to verify reception. On 2026-10-26, room 22101382 authenticated and delivered a real `你好` message while offline (`live_status: 0`). Real gifts have not been tested. This verifies the community receiver, not the official Open Live integration; see README.md for that integration's separate credentials and gaps.

## 7. Chat readback and a cooler voice

In **Replies**, enable **语音先念弹幕核心，再回答**. In **Voice**, use **Cool & composed**, audition, then save. A chat such as `主播，请问今天累了怎么办？` is spoken as `今天累了怎么办？` followed by the answer. The on-screen answer is unchanged. This short extract is local, with no additional LLM request.

Offline regression (no provider calls):

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_speech_delivery.py -v
```

One real GLM + current voice conversation against the running local studio:

```powershell
.\.venv\Scripts\python.exe scripts/test_readback_voice.py
```

The live check sends a synthetic message and uses the currently selected model and voice service. It verifies that the first audio segment prepends the chat once, preserves the answer, and returns nonempty audio. It records client-observed first audio bytes, not the time a speaker becomes audible, in `data/qa/readback-voice-test.json`.

MiniMax is optional: put its key after `MINIMAX_API_KEY=` in the project `.env`, restart, select MiniMax in Voice, and audition. Missing credentials produce a setup message without changing the saved free voice. MiniMax protocol tests use mocks; no paid MiniMax synthesis has been performed in this workspace.

## 8. Try GLM-TTS with the existing BigModel key

1. Install updated dependencies: `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.
2. Reuse `GLM_API_KEY` in `.env`; do not paste credentials into the controller. Start the server with `.\.venv\Scripts\python.exe main.py`.
3. Open **http://127.0.0.1:8000/control → Voice**, select **GLM-TTS · 自然语音**, then **彤彤**. Start with speed **−4%** and **Natural** delivery. Click **Audition voice** to audition the draft; **Apply & save** enables it for conversation.
4. Enable preview sound and send `主播，请问今天累了怎么办？`. The short chat core should precede the answer, with the mouth following actual playback.
5. For a measured conversation, run `scripts/test_readback_voice.py` as in section 7. A standalone short paid voice sample can be generated with `.\.venv\Scripts\python.exe scripts/test_glm_voice.py tongtong`; it saves `data/qa/glm-tongtong-sample.mp3` and latency in `data/qa/glm-auditions.json`.

GLM-TTS costs ¥2/10,000 characters at the documented 2026-10-26 rate; auditions also count. The shared key authenticates both products, but TTS requires available paid balance/quota. The UI readiness indicator only means the key is configured. GLM pitch/emotion sliders are hidden because the API does not expose those parameters. Switch back to **Edge** and save for free voice. Automated `test_glm_tts.py` tests use synthetic PCM, never a paid API.

## 9. Tune and test MiMo voices

1. Add your Xiaomi key after `MIMO_API_KEY=` in `.env`, then restart with `.\.venv\Scripts\python.exe main.py`.
2. Open **Voice → MiMo · 自定义表达** and select **茉莉** or **冰糖**.
3. Edit **Voice direction · 声音风格**, for example `成年女性，声线偏低，清冷从容，略带气声。语速稍慢，停顿自然，表达亲近而克制，咬字清晰。` The direction guides synthesis; it is not spoken. Leave it blank for the voice's natural delivery.
4. Click **Audition voice** to test the draft, then **Apply & save**. Existing GLM/Edge choices remain available. MiMo's unsupported numeric sliders are hidden; describe pacing and tone in the direction field instead.
5. Turn on stage preview sound and run `.\.venv\Scripts\python.exe scripts/test_readback_voice.py` for one measured real GLM-text + MiMo-voice conversation.

For standalone samples, run `.\.venv\Scripts\python.exe scripts/test_mimo_voice.py 茉莉 冰糖`. Samples and first-MP3 timings are saved under `data/qa/`. These are real API calls; MiMo currently lists TTS as temporarily free, subject to its future pricing and account limits. `tests/test_mimo_tts.py` uses local synthetic audio only and checks progressive playback, truncated-stream rejection, separate style/text, caching and credential isolation.

## 10. Subtitle duration and conversation music

Open **Scene → Subtitles & music**. Each spoken subtitle appears when its matching audio actually starts playing, stays through playback, then hides after the selected hold (default 4 seconds, adjustable 1–20). Text arriving from the LLM or a voice URL still loading does not reveal it early. Text-only mode displays replies directly; silent thumbnails show captions when speech is ready. Music defaults to 18% volume and fades to one quarter of that during actual speech, then returns over 1.2 seconds. Disable it or adjust its volume in the same panel; Apply & save updates open outputs.

The bundled 48-second electric-piano lounge loop is an original local synthesis, with no music API, external stream, or music subscription. Rebuild it with `scripts/build_conversation_music.py`. Controller thumbnails never autoplay music; the Scene panel includes a separate audition player. Refresh each existing Bilibili/OBS browser source once after this code update. If a browser blocks playback, click the full stage once to enable sound.

## 11. Quiet-room warm-up

In **Actions → Quiet-room warm-up**, enable occasional speech, choose the quiet delay (default 45 seconds) and minimum gap between lines (default 90 seconds). These controls are independent of idle movement. The server rotates eight short dragon-themed lines using the current voice and its speech cache, with no LLM request. It only prepares a line when an output is connected, chat is quiet, and no viewer reply or event is waiting. New chat cancels a line still being synthesized; a short line already playing finishes naturally before the queued viewer answer. Warm-up speech uses the same subtitles and automatic music ducking as conversation.

Watch `warmups` in `/api/status` or leave the room quiet for the configured delay. Offline scheduling, cancellation, non-repetition and no-output tests: `.\.venv\Scripts\python.exe -m unittest tests.test_warmup -v`.
