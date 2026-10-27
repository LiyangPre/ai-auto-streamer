class StudioControls {
    constructor() {
        this.$ = id => document.getElementById(id);
        this.form = this.$('direction-form');
        this.dialog = this.$('direction-dialog');
        this.$('voice-audition-player').hidden = true;
        this.tab = 'identity';
        this.media = new SharedMediaControls(this);
        this.capture = new MusicCaptureControls(this);
        this.labels = {normal:'平静',happy:'开心',sad:'难过',angry:'生气',surprised:'惊讶',thinking:'思考'};
        document.querySelectorAll('[data-open-direction]').forEach(button => button.addEventListener('click', () => this.open(button.dataset.openDirection)));
        document.querySelectorAll('[data-direction-tab]').forEach(button => button.addEventListener('click', () => this.selectTab(button.dataset.directionTab)));
        this.$('direction-dialog').querySelector('.direction-close').addEventListener('click', () => this.dialog.close());
        this.dialog.addEventListener('close', () => { this.auditionGeneration = (this.auditionGeneration || 0) + 1; this.$('voice-audition-player').pause(); this.$('scene-music-preview').pause(); this.restorePreview(); this.$('scene-preview-frame').removeAttribute('src'); });
        this.form.addEventListener('input', event => {
            if(event.target.type==='file')return;
            this.outputs();
            if(event.target.id.startsWith('scene-'))this.previewScene();
            if (!event.target.id.startsWith('director-') && !event.target.id.startsWith('voice-preview-')) {
                this.dirty = true; this.status('Unsaved changes · 尚未保存');
            }
        });
        this.form.addEventListener('change', () => {this.outputs();if(this.tab==='scene')this.previewScene();});
        this.form.addEventListener('submit', event => { event.preventDefault(); this.save(); });
        this.$('direction-discard').addEventListener('click', () => this.load());
        this.$('latency-preset').addEventListener('click', () => {
            this.$('reply-window').value = .15; this.$('reply-cooldown').value = 0;
            this.$('reply-length').value = 40; this.$('reply-tokens').value = 96;
            this.$('voice-delivery').value = 'fast'; this.$('voice-expressive').checked = false;
            this.dirty = true; this.outputs(); this.status('Low latency preset · 保存后生效');
        });
        this.$('voice-preview-btn').addEventListener('click', () => this.audition());
        this.$('voice-cool-preset').addEventListener('click', () => {
            this.$('voice-select').value = 'zh-CN-XiaoxiaoNeural';
            this.$('minimax-voice-select').value = 'female-yujie';
            this.$('glm-voice-select').value = 'tongtong';
            this.$('mimo-voice-select').value = '茉莉';
            this.$('mimo-voice-style').value = '成年女性，声线偏低，清冷从容，略带气声。语速稍慢，停顿自然，表达亲近而克制，咬字清晰。';
            this.$('voice-rate').value = -4; this.$('voice-pitch').value = -3;
            this.$('minimax-pitch').value = 0; this.$('voice-volume').value = 0;
            this.$('voice-expressive').checked = false; this.$('voice-delivery').value = 'natural';
            this.dirty = true; this.outputs(); this.status('Cool & composed · 先试听，保存后生效');
        });
        this.$('voice-audition-player').addEventListener('ended', () => this.restorePreview());
        this.$('voice-audition-player').addEventListener('pause', () => this.restorePreview());
        this.$('voice-audition-player').addEventListener('play', () => this.mutePreview());
        this.$('director-send').addEventListener('click', async () => {
            const text = this.$('director-text').value.trim();
            if (text.length < 2) { this.status('先写一句要播出的台词。', true); return; }
            try { await this.request('/api/direct-reply', {text, emotion:this.$('director-emotion').value}); this.status('台词已加入直播语音队列'); }
            catch (error) { this.status(error.message, true); }
        });
        this.setupScene();
        this.load();
    }
    async request(url, data) {
        const headers = {'Content-Type':'application/json'};
        if (url === '/api/studio' && data !== undefined && this.profileContext) {
            headers['X-Profile-ID'] = this.profileContext.active_id;
            headers['X-Profile-Revision'] = String(this.profileContext.revision);
        }
        const response = await fetch(url, data === undefined ? {} : {method:'POST', headers, body:JSON.stringify(data), signal:AbortSignal.timeout(30000)});
        const result = await response.json();
        if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '设置未通过验证，请检查输入内容。');
        return result;
    }
    async load() {
        try {
            const data = await this.request('/api/studio');
            await this.media.load();
            this.$('voice-select').replaceChildren();
            data.voices.forEach(voice => { const option = document.createElement('option'); option.value = voice.id; option.textContent = voice.name; this.$('voice-select').append(option); });
            this.$('minimax-voice-select').replaceChildren();
            data.minimax_voices.forEach(voice => { const option = document.createElement('option'); option.value = voice.id; option.textContent = voice.name; this.$('minimax-voice-select').append(option); });
            this.$('glm-voice-select').replaceChildren();
            data.glm_voices.forEach(voice => { const option = document.createElement('option'); option.value = voice.id; option.textContent = voice.name; this.$('glm-voice-select').append(option); });
            this.$('mimo-voice-select').replaceChildren();
            data.mimo_voices.forEach(voice => { const option = document.createElement('option'); option.value = voice.id; option.textContent = voice.name; this.$('mimo-voice-select').append(option); });
            this.$('mimo-provider-status').textContent = data.mimo_ready
                ? '已配置 MiMo 密钥 · 可以试听，保存后用于直播。'
                : '在控制台 API connections 中保存 MiMo 密钥后即可试听。';
            this.$('glm-provider-status').textContent = data.glm_ready
                ? '已配置 GLM 密钥 · 语音另行计费。'
                : '在控制台 API connections 中保存 GLM 密钥，可复用 GLM 文本密钥。';
            this.$('voice-provider-status').textContent = data.minimax_ready
                ? 'MiniMax 已配置 · 试听与直播语音按量计费。'
                : 'MiniMax 未配置 · 在控制台 API connections 中保存 MiniMax 密钥。';
            this.$('emotion-mapping-fields').replaceChildren();
            for (const [emotion,label] of Object.entries(this.labels)) {
                const field = document.createElement('label'); field.textContent = label;
                const select = document.createElement('select'); select.id = `map-${emotion}`;
                for (const action of data.actions) {
                    const option = document.createElement('option'); option.value = action; option.textContent = window.controlPanel?.actions[action]?.[1] || action; select.append(option);
                }
                field.append(select); this.$('emotion-mapping-fields').append(field);
            }
            this.$('direction-action-buttons').replaceChildren();
            for (const action of data.actions) {
                const button = document.createElement('button'); button.type = 'button'; button.className = 'quick-btn';
                button.textContent = window.controlPanel?.actions[action]?.[1] || action;
                button.addEventListener('click', async () => {
                    try { await this.request('/api/test-event', {type:'action_test',action}); this.status(`预览动作 · ${button.textContent}`); }
                    catch (error) { this.status(error.message,true); }
                });
                this.$('direction-action-buttons').append(button);
            }
            this.adopt(data); this.loaded = true;
        } catch (error) { this.status('设置读取失败，请刷新页面。',true); }
    }
    async open(tab) {
        if (!this.loaded) await this.load();
        this.selectTab(tab); if (!this.dialog.open) this.dialog.showModal();
    }
    selectTab(tab) {
        this.tab = tab;
        this.updateHeading();
        document.querySelectorAll('[data-direction-panel]').forEach(panel => { panel.hidden = panel.dataset.directionPanel !== tab; });
        document.querySelectorAll('[data-direction-tab]').forEach(button => {
            const selected = button.dataset.directionTab === tab; button.classList.toggle('selected',selected); button.setAttribute('aria-pressed',String(selected));
        });
        const frame=this.$('scene-preview-frame');
        if(tab==='scene') {
            if(!frame.getAttribute('src'))frame.src='/?preview=1&overlay=1&muted=1';
            this.previewScene();
        } else frame.removeAttribute('src');
    }
    fill(s) {
        this.auditionGeneration = (this.auditionGeneration || 0) + 1;
        this.$('voice-audition-player').pause();
        this.$('voice-audition-player').hidden = true;
        this.$('voice-preview-status').textContent = '使用当前草稿试听，不改变已保存的声音。';
        this.$('identity-name').value = s.identity.name; this.$('identity-spoken-name').value = s.identity.spoken_name;
        this.$('identity-persona').value = s.identity.persona;
        this.$('reply-style').value = s.reply.style; this.$('reply-length').value = s.reply.max_chars;
        if (![...this.$('reply-tokens').options].some(option => +option.value === s.reply.max_tokens)) {
            const option = document.createElement('option'); option.value = s.reply.max_tokens; option.textContent = `${s.reply.max_tokens} tokens`; this.$('reply-tokens').append(option);
        }
        this.$('reply-tokens').value = s.reply.max_tokens; this.$('reply-questions').checked = s.reply.ask_back;
        this.$('reply-read-chat').checked = s.reply.read_chat;
        this.$('reply-templates').checked = s.reply.templates;
        this.$('reply-window').min = Math.min(.05,s.reply.collection_window);
        this.$('reply-window').max = Math.max(2,s.reply.collection_window);
        this.$('reply-window').step = 'any';
        this.$('reply-window').value = s.reply.collection_window;
        this.$('reply-cooldown').value = s.reply.cooldown;
        this.$('voice-select').value = s.voice.voice;
        this.$('voice-provider').value = s.voice.provider;
        this.$('glm-voice-select').value = s.voice.glm_voice;
        this.$('mimo-voice-select').value = s.voice.mimo_voice;
        this.$('mimo-model').value = s.voice.mimo_model||'mimo-v2.5-tts-voicedesign';
        this.$('mimo-voice-style').value = s.voice.mimo_style;
        this.$('minimax-voice-select').value = s.voice.minimax_voice;
        this.$('minimax-pitch').value = s.voice.minimax_pitch;
        for (const key of ['rate','pitch','volume']) this.$(`voice-${key}`).value = s.voice[key];
        this.$('voice-enabled').checked = s.voice.enabled; this.$('voice-expressive').checked = s.voice.expressive;
        this.$('voice-delivery').value = s.voice.delivery;
        this.$('actions-auto').checked = s.actions.automatic; this.$('actions-idle').checked = s.actions.idle_enabled;
        this.$('warmup-enabled').checked=s.actions.warmup_enabled;
        this.$('warmup-delay').value=s.actions.warmup_delay;
        this.$('warmup-interval').value=s.actions.warmup_interval;
        this.$('warmup-lines').value=(s.actions.warmup_lines || []).join('\n');
        this.$('actions-intensity').value = s.actions.intensity;
        for (const [emotion,action] of Object.entries(s.actions.emotion_map)) this.$(`map-${emotion}`).value = action;
        this.fillScene(s.scene);
        this.dirty = false; this.outputs(); this.status('Saved locally · 当前设置');
    }
    voice() {
        return {voice:this.$('voice-select').value, rate:+this.$('voice-rate').value, pitch:+this.$('voice-pitch').value,
            provider:this.$('voice-provider').value, minimax_voice:this.$('minimax-voice-select').value, minimax_pitch:+this.$('minimax-pitch').value,
            glm_voice:this.$('glm-voice-select').value,
            mimo_voice:this.$('mimo-voice-select').value, mimo_style:this.$('mimo-voice-style').value, mimo_model:this.$('mimo-model').value,
            volume:+this.$('voice-volume').value, enabled:this.$('voice-enabled').checked,
            expressive:this.$('voice-expressive').checked, delivery:this.$('voice-delivery').value};
    }
    collect() {
        return {identity:{name:this.$('identity-name').value, spoken_name:this.$('identity-spoken-name').value, persona:this.$('identity-persona').value},
            reply:{style:this.$('reply-style').value, max_chars:+this.$('reply-length').value, max_tokens:+this.$('reply-tokens').value,
                ask_back:this.$('reply-questions').checked, read_chat:this.$('reply-read-chat').checked, templates:this.$('reply-templates').checked,
                collection_window:+this.$('reply-window').value, cooldown:+this.$('reply-cooldown').value},
            voice:this.voice(), scene:this.scene(), actions:{automatic:this.$('actions-auto').checked, idle_enabled:this.$('actions-idle').checked,
                warmup_enabled:this.$('warmup-enabled').checked,warmup_delay:+this.$('warmup-delay').value,warmup_interval:+this.$('warmup-interval').value,
                warmup_lines:this.$('warmup-lines').value.split('\n').map(line=>line.trim()).filter(Boolean),
                intensity:+this.$('actions-intensity').value, emotion_map:Object.fromEntries(Object.keys(this.labels).map(emotion => [emotion,this.$(`map-${emotion}`).value]))}};
    }
    outputs() {
        const provider = this.$('voice-provider').value;
        this.$('mimo-voice-select').disabled=this.$('mimo-model').value==='mimo-v2.5-tts-voicedesign';
        document.querySelectorAll('[data-voice-provider]').forEach(field => {field.hidden = field.dataset.voiceProvider !== provider;});
        this.$('voice-expressive-field').hidden = ['glm-tts','mimo-tts'].includes(provider);
        this.$('voice-numeric-controls').hidden = provider === 'mimo-tts';
        this.$('voice-volume-field').hidden = provider === 'mimo-tts';
        this.$('minimax-pitch-value').textContent = this.$('minimax-pitch').value + ' 半音';
        for (const [id,suffix] of [['reply-length','字'],['reply-window','s'],['reply-cooldown','s'],['voice-rate','%'],['voice-pitch','Hz'],['voice-volume','%']]) {
            this.$(`${id}-value`).textContent = `${this.$(id).value}${suffix}`;
        }
        this.$('actions-intensity-value').textContent = `${Math.round(+this.$('actions-intensity').value * 100)}%`;
        for(const id of ['warmup-delay','warmup-interval']) {
            this.$(`${id}-value`).textContent=this.$(id).value+'s';
            this.$(id).disabled=!this.$('warmup-enabled').checked;
        }
        for(const id of ['weather','dim','scale'])this.$(`scene-${id}-value`).textContent=Math.round(+this.$(`scene-${id}`).value*100)+'%';
        for(const id of ['x','y'])this.$(`scene-${id}-value`).textContent=(+this.$(`scene-${id}`).value>0?'+':'')+this.$(`scene-${id}`).value+'%';
        const transparent=this.$('scene-background-select').value==='transparent';
        const car=this.$('scene-background-select').value==='car';
        this.$('car-scene-controls').hidden=!car;
        this.$('car-scene-hint').hidden=!car;
        this.$('car-drive-controls').hidden=!car;
        this.$('scene-car-vibration').disabled=!this.$('scene-car-motion').checked;
        this.$('scene-preview-frame').parentElement.classList.toggle('car-preview',car);
        for(const id of ['scene-scale','scene-x','scene-y','scene-reset'])this.$(id).disabled=car;
        this.$('scene-position-pad').hidden=car;
        this.$('scene-effect').disabled=transparent||car;this.$('scene-dim').disabled=transparent;
        this.$('scene-weather').disabled=transparent||car||this.$('scene-effect').value==='none';
        for(const id of ['scene-companions','scene-blur','scene-blur-toggle'])this.$(id).disabled=transparent;
        this.$('scene-companions').disabled=transparent||car;
        const blur=+this.$('scene-blur').value;
        this.$('scene-blur-value').textContent=blur?`${blur} / 12`:'Off';
        this.$('scene-blur-toggle').setAttribute('aria-pressed',String(blur>0));
        this.$('scene-subtitle-value').textContent=this.$('scene-subtitle').value+'s';
        this.$('scene-music-volume-value').textContent=Math.round(+this.$('scene-music-volume').value*100)+'%';
        this.$('scene-music-volume').disabled=!this.$('scene-music-enabled').checked;
        this.capture.update();
    }
    async save() {
        if(this.media.busy)return false;
        this.$('direction-save').disabled = true;
        try {
            const result = await this.request('/api/studio', this.collect()); this.adopt(result);
            this.status('Applied & saved · 已生效，无需重启'); window.controlPanel?.loadConfig(); return true;
        } catch (error) { this.status(error.message,true); return false; }
        finally { this.$('direction-save').disabled = false; }
    }
    adopt(data) {
        if (data.profile_context && this.profileContext?.revision > data.profile_context.revision) return;
        this.profileContext = data.profile_context || this.profileContext;
        window.characterProfiles?.update(this.profileContext);
        this.saved = structuredClone(data.settings); this.fill(this.saved); this.updateHeading();
    }
    handleProfileEvent(data) {
        if (!data.profile_context) return;
        window.characterProfiles?.update(data.profile_context);
        if (!this.loaded || data.profile_context.revision <= this.profileContext?.revision) return;
        if (this.dirty) {
            this.status('另一窗口更新了设置。草稿已保留；请 Discard edits 重新读取后再编辑。',true);
            return;
        }
        if (data.settings) this.adopt(data);
        else this.profileContext = data.profile_context;
    }
    updateHeading() {
        const shared = this.tab === 'scene';
        this.$('direction-scope').textContent = shared ? 'SHARED STAGE · 全角色共用' : 'CHARACTER PROFILE · 当前角色';
        this.$('direction-title').textContent = shared ? 'One stage. Many personalities.' : (this.saved?.identity.name || 'Character') + ' · 角色档案';
        this.$('direction-description').textContent = shared ? '场景、小伙伴、字幕与音乐独立保存，切换角色时保留。' : '身份、回应、声音、动作和暖场台词跟随这个角色。';
    }
    async audition() {
        const generation = this.auditionGeneration = (this.auditionGeneration || 0) + 1;
        const player = this.$('voice-audition-player'); player.pause();
        this.$('voice-preview-btn').disabled = true; this.$('voice-preview-status').textContent = '正在准备试听…';
        this.mutePreview();
        try {
            const result = await this.request('/api/voice-preview', {voice:this.voice(),text:this.$('voice-preview-text').value});
            if (!this.dialog.open || generation !== this.auditionGeneration) return;
            player.src = result.audio_url;
            player.hidden = false;
            this.$('voice-preview-status').textContent = '试听已准备好，可播放、暂停或重新选择音色。';
            await player.play().catch(() => { this.$('voice-preview-status').textContent = '请点击播放器的播放按钮开始试听。'; this.restorePreview(); });
        } catch (error) { this.$('voice-preview-status').textContent = error.message; this.restorePreview(); }
        finally { this.$('voice-preview-btn').disabled = false; }
    }
    mutePreview() {
        if (window.controlPanel?.sound) {
            this.previewMuted = true; this.$('stage-preview').contentWindow.postMessage({type:'preview_mute',muted:true},location.origin);
        }
    }
    restorePreview() {
        if (this.previewMuted && window.controlPanel?.sound) this.$('stage-preview').contentWindow.postMessage({type:'preview_mute',muted:false},location.origin);
        this.previewMuted = false;
    }
    scene() {
        const backdrop=this.$('scene-background-select').value;
        return StageScene.normalize({background:backdrop.startsWith('media:')?'custom':backdrop,
            background_asset:backdrop.startsWith('media:')?backdrop.slice(6):null,
            music_asset:this.$('scene-music-track').value||null,effect:this.$('scene-effect').value,
            weather_amount:+this.$('scene-weather').value,dim:+this.$('scene-dim').value,
            companions:this.$('scene-companions').checked,bottom_blur:+this.$('scene-blur').value,
            car_motion:this.$('scene-car-motion').checked,car_vibration:this.$('scene-car-vibration').checked,
            subtitle_seconds:+this.$('scene-subtitle').value,music_enabled:this.$('scene-music-enabled').checked,
            music_source:this.$('scene-music-source').value,
            music_volume:+this.$('scene-music-volume').value,
            character_x:+this.$('scene-x').value,character_y:+this.$('scene-y').value,character_scale:+this.$('scene-scale').value});
    }
    fillScene(value) {
        const s=StageScene.normalize(value);
        this.media.fill(s);this.$('scene-effect').value=s.effect;
        this.$('scene-companions').checked=s.companions;
        this.$('scene-car-motion').checked=s.car_motion;this.$('scene-car-vibration').checked=s.car_vibration;
        this.$('scene-subtitle').value=s.subtitle_seconds;this.$('scene-music-enabled').checked=s.music_enabled;
        this.$('scene-music-source').value=s.music_source;
        this.$('scene-music-volume').value=s.music_volume;
        for(const [id,key] of [['weather','weather_amount'],['dim','dim'],['blur','bottom_blur'],['x','character_x'],['y','character_y'],['scale','character_scale']])this.$(`scene-${id}`).value=s[key];
        this.previewScene();
    }
    previewScene() {
        if(this.tab!=='scene')return;
        this.$('scene-preview-frame').contentWindow?.postMessage({type:'scene_preview',scene:this.scene()},location.origin);
    }
    setupScene() {
        this.form.querySelectorAll('[data-car-expression]').forEach(button=>button.addEventListener('click',()=>{
            this.$('scene-preview-frame').contentWindow?.postMessage({type:'car_expression',expression:button.dataset.carExpression},location.origin);
        }));
        this.$('scene-background-select').addEventListener('change',()=>{
            const background=this.$('scene-background-select').value;
            if(background!=='transparent')this.$('scene-effect').value=background==='car'||background.startsWith('media:')?'none':background==='palace'?'sparkles':'clouds';
            this.dirty=true;this.outputs();this.previewScene();
        });
        this.$('scene-blur-toggle').addEventListener('click',()=>{
            this.$('scene-blur').value=+this.$('scene-blur').value>0?0:4;
            this.dirty=true;this.outputs();this.previewScene();this.status('Preview only · 保存后同步到直播');
        });
        window.addEventListener('message',event=>{
            if(event.origin===location.origin&&event.source===this.$('scene-preview-frame').contentWindow&&event.data?.type==='renderer_ready')this.previewScene();
        });
        this.$('scene-reset').addEventListener('click',()=>{
            this.fillScene({...this.scene(),character_x:0,character_y:0,character_scale:1});
            this.dirty=true;this.outputs();this.status('Position reset · 保存后生效');
        });
        const pad=this.$('scene-position-pad');let drag;
        const move=(x,y)=>{
            this.$('scene-x').value=Math.max(-45,Math.min(45,Math.round(x)));
            this.$('scene-y').value=Math.max(-35,Math.min(35,Math.round(y)));
            this.dirty=true;this.outputs();this.previewScene();this.status('Preview only · 保存后同步到直播');
        };
        pad.addEventListener('pointerdown',event=>{
            if(event.button!==0||this.$('scene-background-select').value==='car')return;
            const box=pad.getBoundingClientRect();drag={id:event.pointerId,x:event.clientX,y:event.clientY,
                startX:+this.$('scene-x').value,startY:+this.$('scene-y').value,w:box.width,h:box.height};
            pad.setPointerCapture(event.pointerId);pad.classList.add('dragging');
        });
        pad.addEventListener('pointermove',event=>{if(drag&&event.pointerId===drag.id)move(drag.startX+(event.clientX-drag.x)/drag.w*100,drag.startY+(event.clientY-drag.y)/drag.h*100);});
        const end=()=>{drag=null;pad.classList.remove('dragging');};
        pad.addEventListener('pointerup',end);pad.addEventListener('pointercancel',end);pad.addEventListener('lostpointercapture',end);
        pad.addEventListener('keydown',event=>{
            if(this.$('scene-background-select').value==='car')return;
            const deltas={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]};
            const delta=deltas[event.key];if(!delta)return;event.preventDefault();
            const step=event.shiftKey?5:1;move(+this.$('scene-x').value+delta[0]*step,+this.$('scene-y').value+delta[1]*step);
        });
    }
    status(message,error=false) { this.$('direction-status').textContent = message; this.$('direction-status').classList.toggle('error',error); }
}
window.addEventListener('DOMContentLoaded', () => { window.studioControls = new StudioControls(); });
