// ---- Smooth numeric interpolation for action params ----
class SmoothAnimator {
    constructor(lerpSpeed = 0.08) {
        this.current = {};
        this.target = {};
        this.lerpSpeed = lerpSpeed;
    }

    setTarget(params) {
        for (const key in params) {
            this.target[key] = params[key];
        }
    }

    update() {
        for (const key in this.target) {
            if (this.current[key] === undefined) {
                this.current[key] = this.target[key];
            } else {
                const diff = this.target[key] - this.current[key];
                if (Math.abs(diff) > 0.001) {
                    this.current[key] += diff * this.lerpSpeed;
                } else {
                    this.current[key] = this.target[key];
                }
            }
        }
        return this.current;
    }

    reset() {
        this.current = {};
        this.target = {};
    }
}

// ---- Sprite-based cartoon renderer (default, no external deps) ----
// Driven entirely by action objects: action.sprite (png) + action.sprite_motion (transform).
class SpriteRenderer {
    constructor(canvas, config) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.spriteDir = (config && config.sprite_dir) || 'avatar';
        this.spriteMouth = config?.sprite_mouth;
        this.choreography = new CharacterMotion(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
        this.face = new CharacterFace();
        this.sprites = {};
        this.targetSprite = 'neutral';
        this.prevSprite = null;
        this.crossfade = 1;
        this.motion = 'breathe';
        this.time = 0;
        this.isSpeaking = false;
        this.ready = false;
        this.rigUrl = config?.rig_url;

        this._onResize = () => this.resize();
        window.addEventListener('resize', this._onResize);
    }

    async load() {
        if (this.rigUrl && window.CutoutRig) {
            try { this.rig=await CutoutRig.load(this.rigUrl); this.resize(); this.ready=true; return true; }
            catch(error) { console.warn('2D rig unavailable, using the original artwork:',error); }
        }
        const names = this.spriteMouth ? ['neutral'] : ['neutral', 'talk', 'happy', 'sad', 'angry', 'surprised', 'think', 'bow'];
        await Promise.all(names.map((n) => this._loadSprite(n)));
        this.resize();
        this.ready = Object.keys(this.sprites).length > 0;
        return this.ready;
    }

    _loadSprite(name) {
        return new Promise((resolve) => {
            const img = new Image();
            img.onload = () => { this.sprites[name] = img; resolve(true); };
            img.onerror = () => resolve(false);
            img.src = `/static/${this.spriteDir}/${name}.png`;
        });
    }

    resize() {
        const layout = getComputedStyle(this.canvas);
        const w = parseFloat(layout.width) || this.canvas.clientWidth || window.innerWidth;
        const h = parseFloat(layout.height) || this.canvas.clientHeight || window.innerHeight;
        // Keep controller thumbnails light; full output retains a 1080p backing
        // buffer even in a smaller browser source, bounded at 2160p for HiDPI.
        const preview = new URLSearchParams(window.location.search).has('preview');
        const dpr = preview ? Math.min(window.devicePixelRatio || 1, 1.5)
            : Math.min(2160 / h, Math.max(window.devicePixelRatio || 1, 1080 / h));
        this.pixelRatio = dpr;
        this.canvas.width = Math.round(w * dpr);
        this.canvas.height = Math.round(h * dpr);
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        this.ctx.imageSmoothingEnabled = true;
        this.ctx.imageSmoothingQuality = 'high';
        this.viewW = w;
        this.viewH = h;
    }

    setAction(action) {
        if (!action) return;
        this.choreography.setAction(action);
        this.face.setAction(action);
        if (this.rig) return;
        const requested = action.sprite || 'neutral';
        const sprite = this.sprites[requested] ? requested : 'neutral';
        if (sprite !== this.targetSprite) {
            this.prevSprite = this.targetSprite;
            this.targetSprite = sprite;
            this.crossfade = 0;
        }
        if (action.sprite_motion) this.motion = action.sprite_motion;
    }

    setSpeaking(v) {
        this.isSpeaking = !!v;
    }

    render(params, dt) {
        if (!this.ready) return;
        this.time += dt;
        if (this.crossfade < 1) {
            this.crossfade = Math.min(1, this.crossfade + dt * 6);
        }

        const ctx = this.ctx;
        const W = this.viewW;
        const H = this.viewH;
        ctx.clearRect(0, 0, W, H);

        if (this.rig) {
            const pose=this.choreography.sample(dt,params?.mouthOpen,this.isSpeaking);
            const singingPose={...pose};
            this.singBlend=(this.singBlend||0)+((params.moduleSway?.amount||0)-(this.singBlend||0))*(1-Math.exp(-dt*5));
            if(params.moduleSway)this.singPhase=params.moduleSway.phase;
            if(this.singBlend>.001&&!this.choreography.reducedMotion){
                const weight=this.singBlend*Math.max(0,Math.min(1,this.choreography.intensity));
                singingPose.headRoll=Math.max(-.065,Math.min(.065,pose.headRoll+Math.sin(this.singPhase||0)*.012*weight));
                singingPose.headPitch=Math.max(-.02,Math.min(.055,pose.headPitch+Math.sin((this.singPhase||0)*2)*.004*weight));
            }
            this.rig.draw(ctx,W,H,singingPose,params?.mouthOpen||pose.mouth,this.choreography.reducedMotion,this.face.sample(dt),params?.mouthRound||0);
            return;
        }

        const targetImg = this.sprites[this.targetSprite] || this.sprites.neutral;
        if (!targetImg) return;

        const pose = this.choreography.sample(dt, params?.mouthOpen, this.isSpeaking);

        // Fit sprite to view by height
        const ratio = targetImg.width / targetImg.height;
        let drawH = H * 0.90;
        let drawW = drawH * ratio;
        if (drawW > W * 0.92) { drawW = W * 0.92; drawH = drawW / ratio; }

        const cx = W / 2 + pose.x * drawW;
        const cy = H / 2 + pose.y * drawH;

        ctx.save();
        const halfW = drawW / 2;
        const halfH = drawH / 2;
        // Anchor the dress hem instead of rotating a floating flat card.
        ctx.translate(cx, cy + halfH);
        ctx.rotate(pose.roll);
        ctx.scale(1 + pose.pulse, 1 + pose.pulse - pose.bow);
        ctx.translate(0, -halfH);

        // Fade out previous sprite during crossfade
        const prevImg = this.prevSprite ? this.sprites[this.prevSprite] : null;
        if (prevImg && this.crossfade < 1) {
            ctx.globalAlpha = 1 - this.crossfade;
            this.drawSprite(prevImg, drawW, drawH, pose);
        }

        // Draw target sprite
        ctx.globalAlpha = this.crossfade;
        this.drawSprite(targetImg, drawW, drawH, pose);
        if (this.spriteMouth && pose.mouth > 0.06) {
            const mouth = this.spriteMouth;
            ctx.globalAlpha = 0.85;
            ctx.fillStyle = '#875467';
            ctx.beginPath();
            ctx.ellipse((mouth.x - 0.5) * drawW, (mouth.y - 0.5) * drawH,
                mouth.width * drawW / 2, mouth.height * drawH * pose.mouth / 2, 0, 0, Math.PI * 2);
            ctx.fill();
        }

        // Lip-sync: overlay talk sprite scaled by mouth openness (closed-mouth bases only)
        const mouthOpen = (params && params.mouthOpen) || 0;
        const talkImg = this.sprites.talk;
        const lipSyncable = this.targetSprite === 'neutral' || this.targetSprite === 'think';
        if (this.isSpeaking && talkImg && lipSyncable && mouthOpen > 0.02) {
            const openness = Math.min(1, mouthOpen);
            ctx.globalAlpha = this.crossfade * openness;
            ctx.drawImage(talkImg, -halfW, -halfH, drawW, drawH);
        }

        ctx.restore();
        ctx.globalAlpha = 1;
    }

    drawSprite(image, width, height, pose) {
        const ctx = this.ctx;
        if (!this.spriteMouth || this.choreography.reducedMotion) {
            ctx.drawImage(image, -width / 2, -height / 2, width, height);
            return;
        }
        // Warp on an integer pixel grid, then composite once. Drawing fractional
        // strips directly to the stage produces visible alpha seams.
        const dpr = this.pixelRatio || 1;
        const h = Math.min(image.naturalHeight || image.height, Math.max(40, Math.round(height * dpr)));
        const w = Math.max(4, Math.round(h * image.width / image.height));
        let cache = this.deformationCache;
        if (!cache || cache.image !== image || cache.h !== h || cache.w !== w) {
            const source = document.createElement('canvas');
            source.width = w; source.height = h;
            source.getContext('2d').drawImage(image, 0, 0, w, h);
            const output = document.createElement('canvas');
            const padding = Math.ceil(w * 0.06);
            output.width = w + padding * 2; output.height = h;
            cache = this.deformationCache = {image, source, output, padding, w, h, ctx: output.getContext('2d')};
        }
        const target = cache.ctx, pad = cache.padding;
        target.clearRect(0, 0, cache.output.width, h);
        const bands = 40;
        const left = Math.round(w * 0.34), right = Math.round(w * 0.66);
        for (let i = 0; i < bands; i++) {
            const sy = Math.floor(i * h / bands), end = Math.floor((i + 1) * h / bands);
            const sh = end - sy, y = (i + 0.5) / bands;
            const cloth = CharacterMotion.ease((y - 0.48) / 0.5);
            const drift = Math.round(Math.sin(this.time * 0.9 + y * 4) * w * 0.004 * cloth);
            const wing = Math.sin(Math.PI * Math.max(0, Math.min(1, (y - 0.14) / 0.34)));
            const stretch = 1 + pose.flutter * wing;
            if (y > 0.14 && y < 0.48) {
                const lw = Math.round(left * stretch), rw = Math.round((w - right) * stretch);
                target.drawImage(cache.source, 0, sy, left, sh, pad + left - lw, sy, lw, sh);
                target.drawImage(cache.source, left, sy, right - left, sh, pad + left, sy, right - left, sh);
                target.drawImage(cache.source, right, sy, w - right, sh, pad + right, sy, rw, sh);
            } else {
                target.drawImage(cache.source, 0, sy, w, sh, pad + drift, sy, w, sh);
            }
        }
        ctx.drawImage(cache.output, -width / 2 - pad * width / w, -height / 2,
            cache.output.width * width / w, height);
    }
}

// ---- Live2D renderer (pixi-live2d-display, loaded on demand) ----
function loadScript(src) {
    return new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = src;
        s.onload = resolve;
        s.onerror = () => reject(new Error('Failed to load ' + src));
        document.head.appendChild(s);
    });
}

async function loadLive2DLibs() {
    if (window.PIXI && window.PIXI.live2d) return;
    await loadScript('https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js');
    await loadScript('https://cdn.jsdelivr.net/gh/dylanNew/live2d/webgl/Live2D/lib/live2d.min.js');
    await loadScript('https://cdn.jsdelivr.net/npm/pixi.js@6.5.10/dist/browser/pixi.min.js');
    await loadScript('https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/index.min.js');
}

class Live2DRenderer {
    constructor(canvas, config) {
        this.canvas = canvas;
        this.modelUrl = config.model_url;
        this.pixiApp = null;
        this.model = null;
        this.ready = false;
        this._onResize = () => this.resize();
        window.addEventListener('resize', this._onResize);
    }

    async load() {
        await loadLive2DLibs();
        this.pixiApp = new PIXI.Application({
            view: this.canvas,
            autoStart: true,
            transparent: true,
            resizeTo: window
        });
        this.model = await PIXI.live2d.Live2DModel.from(this.modelUrl);
        this.pixiApp.stage.addChild(this.model);
        this.resize();
        this.ready = true;
        return true;
    }

    resize() {
        if (!this.model || !this.pixiApp) return;
        const sw = this.pixiApp.screen.width;
        const sh = this.pixiApp.screen.height;
        const scale = Math.min(sw / this.model.width, sh / this.model.height) * 0.8;
        this.model.scale.set(scale);
        this.model.x = (sw - this.model.width * scale) / 2;
        this.model.y = (sh - this.model.height * scale) / 2;
    }

    setAction(action) {
        if (!this.ready || !this.model || !action) return;
        try {
            if (action.expression) this.model.expression(action.expression);
            if (action.motion) this.model.motion(action.motion);
        } catch (e) {
            console.warn('Live2D expression/motion failed:', e);
        }
    }

    setSpeaking(v) { /* mouth driven by params */ }

    render(params) {
        if (!this.ready || !this.model) return;
        try {
            const coreModel = this.model.internalModel.coreModel;
            const set = (id, v) => { if (v !== undefined) coreModel.setParameterValueById(id, v); };
            set('ParamMouthOpenY', params.mouthOpen);
            set('ParamMouthForm', params.mouthForm);
            if (params.eyeOpen !== undefined) {
                set('ParamEyeLOpen', params.eyeOpen);
                set('ParamEyeROpen', params.eyeOpen);
            }
            set('ParamEyeBallX', params.eyeBallX);
            set('ParamEyeBallY', params.eyeBallY);
            set('ParamBodyAngleX', params.bodyAngleX);
            set('ParamBodyAngleY', params.bodyAngleY);
            if (params.browY !== undefined) {
                set('ParamBrowLY', params.browY);
                set('ParamBrowRY', params.browY);
            }
        } catch (e) {
            console.warn('Failed to apply params:', e);
        }
    }
}

// ---- Main client ----
class AIStreamerClient {
    constructor() {
        this.ws = null;
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.wsUrl = `${proto}//${window.location.host}/ws`;
        this.reconnectInterval = 3000;
        this.maxReconnectAttempts = 5;
        this.reconnectAttempts = 0;

        this.renderer = null;
        this.audioContext = null;
        this.analyser = null;
        this.voiceEnvelope = new VoiceEnvelope();
        this.currentAudio = null;
        this.audioQueue = [];
        this.audioPlaying = false;
        this.audioEndedResolve = null;
        this.audioGeneration = 0;
        this.muted = new URLSearchParams(location.search).get('muted') === '1';
        this.isPreview = new URLSearchParams(location.search).has('preview');
        this.pingTimer = null;

        this.animator = new SmoothAnimator(0.08);
        this.isSpeaking = false;
        this.lastFrame = performance.now();

        this.emotionIcons = {
            happy: '😊', sad: '😢', angry: '😠',
            surprised: '😲', thinking: '🤔', normal: '😐'
        };

        this.init();
    }

    async init() {
        this.setupUI();
        await this.initRenderer();
        this.modules=new StageModuleHost(this);
        try{await this.modules.init();}catch(error){console.warn('Stage modules unavailable:',error.message);}
        this.connectWebSocket();
        this.startAnimationLoop();
    }

    setupUI() {
        this.elements = {
            spriteCanvas: document.getElementById('avatar-canvas'),
            live2dCanvas: document.getElementById('live2d-canvas'),
            vrmCanvas: document.getElementById('vrm-canvas'),
            fallback: document.getElementById('avatar-fallback'),
            emotionIcon: document.getElementById('emotion-icon'),
            emotionText: document.getElementById('emotion-text'),
            danmakuList: document.getElementById('danmaku-list'),
            responseText: document.getElementById('response-text'),
            wsStatus: document.getElementById('ws-status'),
            statusText: document.getElementById('status-text')
        };
        this.subtitles=new StageSubtitles(this.elements.responseText);
        this.music=new StageMusic({preview:this.isPreview,muted:this.muted});

        if (new URLSearchParams(window.location.search).has('overlay')) {
            document.body.classList.add('overlay-mode');
        }
        if (this.isPreview) document.body.classList.add('preview-mode');
        window.addEventListener('message', event => {
            if (event.origin !== location.origin || event.source !== window.parent || !this.isPreview) return;
            if (event.data?.type === 'preview_mute') {
                this.muted = !!event.data.muted;
                this.music?.setMuted(this.muted);
                this.modules?.setMuted(this.muted);
                if (this.muted) this.stopAudio();
                else this.unlockAudio();
            }
            if(event.data?.type==='scene_preview')this.applyScene(event.data.scene);
            if(event.data?.type==='car_expression'&&this.carScene?.active)this.carScene.setExpression(event.data.expression);
        });
        document.addEventListener('pointerdown', () => this.unlockAudio());
        document.addEventListener('keydown', () => this.unlockAudio());
        window.addEventListener('pagehide', () => {
            this.closed = true; this.stopAudio(); this.stopPingLoop();
            this.scene?.dispose();
            this.carScene?.dispose();
            this.modules?.dispose();
            this.music?.dispose();this.subtitles?.dispose();
            clearTimeout(this.reconnectTimer); this.ws?.close();
        });
        window.addEventListener('pageshow', event => {
            if (event.persisted) { this.closed = false; this.connectWebSocket();this.music?.unlock();if(this.scene){window.addEventListener('resize',this.scene.onResize);this.scene.resize();}if(this.carScene){window.addEventListener('resize',this.carScene.onResize);this.carScene.resize();} }
        });

        this.elements.responseText.classList.add('empty');
        this.elements.responseText.textContent = '等待弹幕...';
        const full=document.getElementById('fullscreen-btn');
        if(new URLSearchParams(location.search).get('controls')==='0')full.hidden=true;
        let controlsTimer;
        const showControls=()=>{document.body.classList.add('show-stage-controls');clearTimeout(controlsTimer);
            controlsTimer=setTimeout(()=>document.body.classList.remove('show-stage-controls'),2200);};
        document.addEventListener('pointermove',showControls);showControls();
        full.addEventListener('click',async()=>{
            try {
                if(document.fullscreenElement)await document.exitFullscreen();
                else await document.documentElement.requestFullscreen();
            } catch {document.getElementById('fullscreen-status').textContent='Use your browser’s fullscreen shortcut, or add this page as an OBS browser source.';}
        });
        document.addEventListener('fullscreenchange',()=>{
            const active=!!document.fullscreenElement;
            full.setAttribute('aria-label',active?'Exit fullscreen':'Enter fullscreen');
            full.querySelector('span').textContent=active?'Exit fullscreen':'Fullscreen';
        });
    }

    async initRenderer() {
        let cfg = { renderer: 'sprite', sprite_dir: 'avatar', vrm_model_url: '' };
        try {
            const resp = await fetch('/api/config');
            if (resp.ok) cfg = await resp.json();
        } catch (e) {
            console.warn('Failed to fetch /api/config, defaulting to sprite:', e);
        }

        let mode = cfg.renderer || 'sprite';
        this.carScene=new CarScene(document.getElementById('car-scene'),{preview:this.isPreview,
            reducedMotion:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches});
        this.scene=new StageScene(document.getElementById('scene-background'),document.getElementById('weather-canvas'),
            document.getElementById('live2d-container'),{transparent:new URLSearchParams(location.search).get('transparent')==='1',
                reducedMotion:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
                companions:document.getElementById('scene-companions'),bottomBlur:document.getElementById('scene-bottom-blur')});
        const sceneOverride=new URLSearchParams(location.search).get('scene');
        this.sceneOverride=sceneOverride==='car'?'car':null;
        this.applyScene(cfg.scene);
        this.characterName = cfg.character || 'AI助手';
        this.activeProfileId = cfg.profile_context?.active_id;
        this.voiceEnabled=cfg.tts_engine!=='none';
        const requested = new URLSearchParams(location.search).get('renderer');
        if (this.isPreview && ['sprite', 'vrm'].includes(requested)) mode = requested;
        if (mode === 'auto') {
            mode = cfg.vrm_model_url ? 'vrm' : (cfg.model_url ? 'live2d' : 'sprite');
        }

        if (mode === 'vrm' && cfg.vrm_model_url) {
            try {
                const { VRMRenderer } = await import('/static/vendor/vrm-renderer.bundle.js');
                this.renderer = new VRMRenderer(this.elements.vrmCanvas, cfg);
                await this.renderer.load();
                this.elements.vrmCanvas.classList.remove('hidden');
                this.renderer.resize();
                this.elements.spriteCanvas.classList.add('hidden');
                this.elements.live2dCanvas.classList.add('hidden');
                this.elements.fallback.classList.add('hidden');
                console.log('Renderer: VRM');
                this.rendererReady('vrm');
                return;
            } catch (e) {
                this.renderer?.dispose?.();
                console.warn('VRM init failed, falling back to sprite:', e);
            }
        }

        if (mode === 'live2d' && cfg.model_url) {
            try {
                this.renderer = new Live2DRenderer(this.elements.live2dCanvas, cfg);
                await this.renderer.load();
                this.elements.live2dCanvas.classList.remove('hidden');
                this.elements.spriteCanvas.classList.add('hidden');
                this.elements.vrmCanvas.classList.add('hidden');
                this.elements.fallback.classList.add('hidden');
                console.log('Renderer: Live2D');
                this.rendererReady('live2d');
                return;
            } catch (e) {
                console.warn('Live2D init failed, falling back to sprite:', e);
            }
        }

        this.renderer = new SpriteRenderer(this.elements.spriteCanvas, cfg);
        const ok = await this.renderer.load();
        if (ok) {
            this.elements.spriteCanvas.classList.remove('hidden');
            this.elements.live2dCanvas.classList.add('hidden');
            this.elements.vrmCanvas.classList.add('hidden');
            this.elements.fallback.classList.add('hidden');
            console.log('Renderer: Sprite (' + cfg.sprite_dir + ')');
            this.rendererReady('sprite');
        } else {
            this.elements.fallback.classList.remove('hidden');
            console.warn('Sprites failed to load, using fallback avatar');
            this.rendererReady('fallback');
        }
    }

    rendererReady(renderer) {
        if (this.isPreview) window.parent.postMessage({ type: 'renderer_ready', renderer, rig:!!this.renderer?.rig }, location.origin);
    }

    connectWebSocket() {
        if (this.closed) return;
        try {
            this.ws = new WebSocket(this.wsUrl);

            this.ws.onopen = () => {
                console.log('WebSocket connected');
                this.updateStatus(true);
                this.reconnectAttempts = 0;
                this.startPingLoop();
            };

            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            };

            this.ws.onclose = () => {
                console.log('WebSocket disconnected');
                this.updateStatus(false);
                this.stopPingLoop();
                this.attemptReconnect();
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        } catch (err) {
            console.error('Failed to connect WebSocket:', err);
            this.attemptReconnect();
        }
    }

    attemptReconnect() {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => this.connectWebSocket(),
            Math.min(15000, 1000 * 2 ** this.reconnectAttempts++));
    }

    startPingLoop() {
        this.stopPingLoop();
        this.pingTimer = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);
    }

    stopPingLoop() {
        if (this.pingTimer) {
            clearInterval(this.pingTimer);
            this.pingTimer = null;
        }
    }

    updateStatus(connected) {
        if (connected) {
            this.elements.wsStatus.classList.remove('disconnected');
            this.elements.wsStatus.classList.add('connected');
            this.elements.statusText.textContent = '已连接';
        } else {
            this.elements.wsStatus.classList.remove('connected');
            this.elements.wsStatus.classList.add('disconnected');
            this.elements.statusText.textContent = '未连接';
        }
    }

    applyScene(value) {
        const settings=StageScene.normalize(this.sceneOverride?{...value,background:this.sceneOverride}:value);
        this.scene?.setSettings(settings);
        this.carScene?.setActive(settings.background==='car'&&!this.scene?.forceTransparent,settings);
        this.subtitles?.setDuration(settings.subtitle_seconds);
        this.music?.configure(settings);
    }

    handleMessage(data) {
        if(this.modules?.handle(data))return;
        switch (data.type) {
            case 'status': if (data.settings) this.applyStudioSettings(data); break;
            case 'settings_updated':
                this.applyStudioSettings(data);
                break;
            case 'thinking_start': this.handleThinkingStart(data); break;
            case 'thinking_end': this.handleThinkingEnd(data); break;
            case 'response_start': this.handleResponseStart(data); break;
            case 'response_chunk': this.handleResponseChunk(data); break;
            case 'response_end': this.handleResponseEnd(data); break;
            case 'response': this.handleFullResponse(data); break;
            case 'audio_segment': if (data.audio_url) this.playAudio(data.audio_url,data.text); break;
            case 'tts_error': this.handleTTSError(); break;
            case 'response_error': this.handleResponseError(); break;
            case 'moderation': this.handleModeration(); break;
            case 'idle_action': this.handleIdleAction(data); break;
        }
    }

    applyStudioSettings(data) {
        const id = data.profile_context?.active_id;
        if (data.profile_changed || (id && this.activeProfileId && id !== this.activeProfileId)) {
            this.stopAudio();
            this.pendingCaption = ''; this.speechFailed = false;
            this.subtitles?.waitForSpeech();
            this.carScene?.setExpression('normal');
        }
        if (id) this.activeProfileId = id;
        this.applyScene(data.settings.scene);
        this.characterName = data.settings.identity.name;
        this.voiceEnabled = data.settings.voice.enabled;
        if (!this.voiceEnabled) {this.stopAudio();if(this.pendingCaption){this.subtitles?.show(this.pendingCaption);this.subtitles?.finish();}}
        if (this.renderer?.choreography) this.renderer.choreography.intensity = data.settings.actions.intensity;
    }

    handleThinkingStart(data) {
        this.pendingCaption='';
        this.speechFailed=false;
        if(this.voiceEnabled)this.subtitles?.waitForSpeech();
        else {
            this.subtitles?.begin();
            this.elements.responseText.classList.add('thinking');
            this.elements.responseText.textContent = '思考中...';
        }
        this.updateEmotion('thinking');
        if (data.action) {
            this.setTargetAction(data.action);
        } else {
            this.setTargetAction({
                mouthOpen: 0, mouthForm: 0, eyeOpen: 0.9,
                eyeBallX: 0.8, eyeBallY: 0.5, bodyAngleX: -3, browY: 0.3
            });
        }
    }

    handleThinkingEnd(data) {
        this.elements.responseText.classList.remove('thinking');
        if (this.currentAction?.name === 'think') {
            this.setTargetAction({name: 'idle', sprite: 'neutral', sprite_motion: 'breathe'});
        }
    }

    handleResponseStart(data) {
        this.pendingCaption='';
        this.speechFailed=false;
        if(this.voiceEnabled)this.subtitles?.waitForSpeech();
        else this.subtitles?.begin();
        this.elements.responseText.classList.remove('thinking');
        if(!this.voiceEnabled)this.elements.responseText.textContent = '';
        this.updateEmotion(data.emotion);
        if (data.action) this.setTargetAction(data.action);
    }

    handleResponseChunk(data) {
        this.pendingCaption=(this.pendingCaption||'')+data.text;
        if(!this.voiceEnabled)this.elements.responseText.textContent = this.pendingCaption;
        else if(this.speechFailed)this.subtitles?.show(this.pendingCaption);
    }

    handleResponseEnd(data) {
        if (data.blocked) {
            this.handleModeration();
            return;
        }
        if (data.audio_url) this.playAudio(data.audio_url,data.full_text);
        if(this.speechFailed&&data.full_text)this.subtitles?.show(data.full_text);
        if (data.full_text) this.addDanmakuToFeed(this.characterName || 'AI助手', data.full_text);
        this.subtitles?.finish();
    }

    handleTTSError() {
        // A failed voice must still leave a readable answer.
        this.speechFailed=true;
        if(this.pendingCaption)this.subtitles?.show(this.pendingCaption);
        this.subtitles?.finish();
    }

    handleResponseError() {
        this.stopAudio();
        this.elements.responseText.classList.remove('thinking');
        this.elements.responseText.textContent = '这次没有收到回复';
        this.elements.responseText.classList.remove('empty');
        this.subtitles?.finish();
    }

    handleModeration() {
        this.stopAudio();
        this.elements.responseText.classList.remove('thinking');
        this.elements.responseText.textContent = '该内容未播出';
        this.elements.responseText.classList.remove('empty');
        this.subtitles?.finish();
    }

    handleFullResponse(data) {
        this.pendingCaption=data.text;
        this.speechFailed=false;
        if(this.voiceEnabled)this.subtitles?.waitForSpeech();
        else {this.subtitles?.begin();this.elements.responseText.textContent = data.text;}
        this.updateEmotion(data.emotion);
        if (data.action) this.setTargetAction(data.action);
        if (data.audio_url) this.playAudio(data.audio_url,data.text);
        this.addDanmakuToFeed(this.characterName || 'AI助手', data.text);
        if(!this.voiceEnabled)this.subtitles?.finish();
    }

    handleIdleAction(data) {
        if (data.action) this.setTargetAction(data.action);
        if (data.action) this.carScene?.setAction(data.action);
        if (data.emotion) this.updateEmotion(data.emotion);
    }

    updateEmotion(emotion) {
        const preset=this.modules?.presentation('chat').expression;
        if(preset&&preset!=='auto')emotion=preset==='neutral'?'normal':preset;
        this.carScene?.setExpression(emotion);
        if(preset&&preset!=='auto')this.renderer?.face?.setExpression(preset);
        const icon = this.emotionIcons[emotion] || this.emotionIcons.normal;
        this.elements.emotionIcon.textContent = icon;
        this.elements.emotionText.textContent = emotion;
    }

    setTargetAction(action) {
        if (!action) return;
        const look=this.modules?.presentation('chat');
        if(look)action={...action,name:look.pose==='auto'?action.name:look.pose,intensity:look.motion_strength};
        this.currentAction = action;

        const params = {
            mouthOpen: action.mouthOpen || 0,
            mouthForm: action.mouthForm || 0,
            eyeOpen: action.eyeOpen !== undefined ? action.eyeOpen : 1,
            eyeBallX: action.eyeBallX || 0,
            eyeBallY: action.eyeBallY || 0,
            bodyAngleX: action.bodyAngleX || 0,
            bodyAngleY: action.bodyAngleY || 0,
            browY: action.browY || 0
        };

        this.animator.setTarget(params);
        if (this.renderer) this.renderer.setAction(action);
        if(look?.expression&&look.expression!=='auto'){
            this.renderer?.face?.setExpression(look.expression,!!action.loop);
            this.carScene?.setExpression(look.expression==='neutral'?'normal':look.expression);
        }
    }

    startAnimationLoop() {
        const loop = (now) => {
            // Cap visible rendering at 30fps and hidden windows at 5fps.
            if (now - this.lastFrame < (document.hidden ? 200 : 1000 / 30)) {
                requestAnimationFrame(loop); return;
            }
            const dt = Math.min(0.1, (now - this.lastFrame) / 1000);
            this.lastFrame = now;
            this.scene?.render(dt,document.hidden);

            const params = { ...this.animator.update() };

            const moduleVoice=this.modules?.voice;
            const songPlaying=moduleVoice?.playing;
            const playing=songPlaying||(this.isSpeaking && this.currentAudio && !this.currentAudio.paused && !this.currentAudio.muted && this.currentAudio.volume>0 &&
                !this.currentAudio.ended && this.audioContext?.state==='running');
            const analyser=songPlaying?moduleVoice.analyser:this.analyser;
            const waveform=songPlaying?moduleVoice.waveform:this.audioWaveform;
            const spectrum=songPlaying?moduleVoice.spectrum:this.audioData;
            if(playing && analyser) {
                analyser.getFloatTimeDomainData(waveform);
                analyser.getByteFrequencyData(spectrum);
            }
            const voice=this.voiceEnvelope.sample(waveform,spectrum,
                (songPlaying?moduleVoice.ctx.sampleRate:this.audioContext?.sampleRate)||48000,dt,!!playing);
            params.mouthOpen=voice.open;params.mouthRound=voice.round;
            this.modules?.frame(params,dt);

            if (this.carScene?.active)this.carScene.render(params,dt,document.hidden);
            else if (this.renderer) this.renderer.render(params, dt);
            requestAnimationFrame(loop);
        };
        requestAnimationFrame(loop);
    }

    stopAudio() {
        this.music?.setSpeaking(false);
        this.subtitles?.setAudioBusy(false);
        this.audioGeneration += 1;
        this.audioQueue = [];
        if (this.audioEndedResolve) {
            this.audioEndedResolve();
            this.audioEndedResolve = null;
        }
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio = null;
        }
        this.isSpeaking = false;
        this.voiceEnvelope?.reset();
        if (this.renderer) this.renderer.setSpeaking(false);
        this.animator.setTarget({ mouthOpen: 0 });
    }

    async playAudio(audioUrl,caption='') {
        if (!audioUrl) return;
        if(this.muted) {
            // Silent thumbnails show ready speech without decoding a second voice.
            if(caption){this.subtitles?.show(caption);this.subtitles?.finish();}
            return;
        }
        // Keep a bounded, recent playback queue if a browser is blocked or slow.
        if (this.audioQueue.length >= 16) this.audioQueue.shift();
        this.audioQueue.push({url:audioUrl,caption});
        this.subtitles?.setAudioBusy(true);
        return this.drainAudio();
    }

    unlockAudio() {
        if (this.muted) return;
        this.modules?.unlock();
        this.music?.unlock();
        this.awaitingGesture = false;
        if (!this.audioContext) {
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            this.analyser = this.audioContext.createAnalyser();
            this.analyser.fftSize = 1024;
            this.analyser.smoothingTimeConstant = 0.15;
            this.audioWaveform = new Float32Array(this.analyser.fftSize);
            this.audioData = new Uint8Array(this.analyser.frequencyBinCount);
            this.analyser.connect(this.audioContext.destination);
        }
        this.audioContext.resume().catch(() => {});
        this.drainAudio();
    }

    async drainAudio() {
        if (this.audioPlaying) return;
        if (this.muted || this.awaitingGesture || !this.audioQueue.length) return;
        this.audioPlaying = true;
        this.subtitles?.setAudioBusy(true);
        const generation = this.audioGeneration;

        try {
            while (this.audioQueue.length && generation === this.audioGeneration) {
                const cue = this.audioQueue.shift(),url=cue.url;
                const audio = new Audio(url);
                if (typeof document !== 'undefined' && document.body) {
                    document.querySelectorAll('audio[data-streamer-voice]').forEach(previous => previous.remove());
                    audio.dataset.streamerVoice = 'active';
                    audio.hidden = true;
                    document.body.appendChild(audio);
                }
                this.currentAudio = audio;
                let source;
                try {
                    if (!this.audioContext) {
                        this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
                        this.analyser = this.audioContext.createAnalyser();
                        this.analyser.fftSize = 1024;
                        this.analyser.smoothingTimeConstant = 0.15;
                        this.audioWaveform = new Float32Array(this.analyser.fftSize);
                        this.audioData = new Uint8Array(this.analyser.frequencyBinCount);
                        this.analyser.connect(this.audioContext.destination);
                    }
                    if (this.audioContext.state === 'suspended') {
                        // A blocked autoplay must not leave the queue worker stuck.
                        this.audioContext.resume().catch(() => {});
                    }
                    if (generation !== this.audioGeneration) break;

                    source = this.audioContext.createMediaElementSource(audio);
                    source.connect(this.analyser);
                    const ended = new Promise(resolve => {
                        this.audioEndedResolve = resolve;
                        audio.onended = resolve;
                        audio.onerror = resolve;
                    });
                    audio.onplaying=()=>{
                        if(generation!==this.audioGeneration)return;
                        this.isSpeaking=true;this.renderer?.setSpeaking(true);this.music?.setSpeaking(true);
                        if(cue.caption){this.subtitles?.show(cue.caption);this.subtitles?.finish();}
                    };
                    audio.onwaiting=audio.onpause=()=>{this.isSpeaking=false;this.voiceEnvelope?.reset();this.renderer?.setSpeaking(false);this.music?.setSpeaking(false);};
                    await audio.play();
                    if (this.audioContext.state !== 'running') throw new DOMException('Audio needs a gesture', 'NotAllowedError');
                    await ended;
                } catch (err) {
                    console.error('Failed to play audio:', err);
                    if (err.name === 'NotAllowedError' && generation === this.audioGeneration) {
                        this.awaitingGesture = true;
                        this.audioQueue.unshift(cue);
                        if (this.isPreview) window.parent.postMessage({type: 'audio_blocked'}, location.origin);
                        break;
                    }
                } finally {
                    audio.pause(); source?.disconnect();
                    // Retain only the latest media element so completed playback
                    // can be inspected; the next segment replaces it.
                    this.isSpeaking = false;
                    this.music?.setSpeaking(false);
                    this.voiceEnvelope?.reset();
                    this.currentAudio = null;
                    this.audioEndedResolve = null;
                    if (this.renderer) this.renderer.setSpeaking(false);
                    this.animator.setTarget({ mouthOpen: 0 });
                }
            }
        } finally {
            this.audioPlaying = false;
            this.subtitles?.setAudioBusy(this.audioQueue.length>0&&!this.awaitingGesture);
            // A new response may arrive while a cancelled worker is unwinding.
            if (this.audioQueue.length && generation !== this.audioGeneration) this.drainAudio();
        }
    }

    addDanmakuToFeed(userName, content) {
        const item = document.createElement('div');
        item.className = 'danmaku-item';
        const user = document.createElement('div');
        user.className = 'danmaku-user';
        user.textContent = userName;
        const text = document.createElement('div');
        text.className = 'danmaku-content';
        text.textContent = content;
        item.append(user, text);
        this.elements.danmakuList.insertBefore(item, this.elements.danmakuList.firstChild);
        while (this.elements.danmakuList.children.length > 20) {
            this.elements.danmakuList.removeChild(this.elements.danmakuList.lastChild);
        }
    }
}

window.addEventListener('DOMContentLoaded', () => {
    window.client = new AIStreamerClient();
});
