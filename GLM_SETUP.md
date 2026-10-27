# GLM-4-Flash with voice

The normal configuration (`config.yaml`, port 8000) uses:

- Provider: `glm`
- Model: `glm-4-flash-250414` (the free Flash model, not FlashX)
- Endpoint: `https://open.bigmodel.cn/api/paas/v4/`
- Voice: Edge-TTS `zh-CN-XiaoxiaoNeural`, neutral pitch and speed
- Streaming text, progressive MP3 delivery, and cached repeated lines
- Intake window: 0.15 seconds; reply cooldown: off

## Add your key locally

1. Create a BigModel key at https://bigmodel.cn/usercenter/proj-mgmt/apikeys.
2. Open `D:\ai auto streamer\.env` in a text editor.
3. Put the key immediately after the equals sign on this line, then save:

```dotenv
GLM_API_KEY=your_actual_bigmodel_key
```

The example above is a placeholder. Enter your key only in the local `.env` file. `.env` is ignored by Git and the browser receives only a configured/not-configured flag. The app does not use an OpenAI key for GLM.

## Start the real model and voice

```powershell
cd 'D:\ai auto streamer'
python main.py
```

If port 8000 is already running, restart that server after saving the key. Environment variables from the launch shell take precedence over `.env`.

Open **http://127.0.0.1:8000/control**. The engine card should show `GLM · Flash` and `glm-4-flash-250414`. Click **Sound off** to enable preview sound. If the browser asks for a gesture, click inside the character preview once.

Try a question such as **今天有点累，给我讲一个关于银龙的小笑话吧**. A plain greeting may use the lightweight template router and will not establish that the API works.

At the top of the controller, open **Identity**, **Replies**, **Voice**, or **Actions**. Voice provides four audition choices plus pitch, speed, volume, and delivery controls. Auditions use your draft without saving it. **Apply & save** updates the running character and stores the settings in `data/studio-settings.json`; no restart is needed. This file overrides the corresponding YAML values on later starts. API key changes still require a restart.

For fast replies, use **Replies → Low latency preset** and save. It selects a 0.15-second collection window, zero cooldown, 40-character replies, a 96-token budget, early speech delivery, and stable pitch. Model and speech network latency remain outside the local application's control.

## Run the live two-turn check

With the server running, open a second terminal:

```powershell
cd 'D:\ai auto streamer'
python scripts/test_glm_voice.py
```

The check sends two non-template conversation turns, verifies that the LLM call counter increases, receives real replies and voice events, and downloads the generated MP3 files to verify they are nonempty. It never prints your key. On success:

```text
PASS: two real GLM conversation turns with voice
```

The transcript and generated audio URLs are saved to `data/qa/glm-voice-test.json`. Browser sound still needs to be enabled to hear playback.

Measure first text and actual first audio bytes with `python scripts/benchmark_latency.py --runs 3`. The report is saved to `data/qa/latency-after.json`. Measurements include intake and external calls, and can vary between runs; first audio data is earlier than audible playback.

The separate offline demo remains at **http://127.0.0.1:8001/control**, launched with `python main.py --config config.local-test.yaml`. It uses mock replies and disables voice.

Official references: [BigModel API compatibility](https://docs.bigmodel.cn/cn/guide/develop/openai/introduction), [model pricing](https://docs.bigmodel.cn/cn/guide/start/pricing).
