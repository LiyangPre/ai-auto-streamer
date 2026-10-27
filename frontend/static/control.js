class ControlPanel {
    constructor() {
        this.ws = null;
        this.retry = 0;
        this.connected = false;
        this.pending = false;
        this.sound = false;
        this.config = {};
        this.$ = id => document.getElementById(id);
        this.emptyFeed = this.$('feed-empty').cloneNode(true);
        this.actions = {
            idle: ['◡', '待机'], talk: ['♧', '说话'], wave: ['〰', '挥手'], nod: ['⌄', '点头'],
            think: ['⋯', '思考'], laugh: ['☺', '轻笑'], cry: ['♧', '难过'], angry: ['ϟ', '生气'],
            surprise: ['✧', '惊讶'], bow: ['⌒', '鞠躬'], shy:['❀','害羞'], heart:['♡','捧心'],
            cheer:['✧','欢呼'], double_wave:['〰','双手挥挥'], peek:['◡','捂脸偷看'],
            present:['↗','介绍'], stretch:['☼','伸懒腰'], curtsy:['⌒','提裙礼']
        };
        this.setupUI();
        this.loadConfig();
        this.connectWebSocket();
    }

    setupUI() {
        this.$('stage-surface').after(this.$('stage-surface').querySelector('.stage-footer'));
        const poseLink=document.createElement('a');
        poseLink.href='/rig-preview'; poseLink.target='_blank'; poseLink.rel='noopener';
        poseLink.className='text-button pose-studio-link'; poseLink.textContent='2D Pose Studio · 关节动作预览 ↗';
        this.$('action-buttons').after(poseLink);
        this.$('message-form').addEventListener('submit', event => { event.preventDefault(); this.sendDanmaku(); });
        this.$('danmaku-input').addEventListener('keydown', event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
                event.preventDefault(); this.sendDanmaku();
            }
        });
        this.$('danmaku-input').addEventListener('input', () => this.updateCharCount());
        document.querySelectorAll('.quick-btn').forEach(button => button.addEventListener('click', () => {
            if (this.pending) return;
            this.$('danmaku-input').value = button.dataset.msg;
            this.updateCharCount(); this.sendDanmaku();
        }));
        this.$('clear-btn').addEventListener('click', () => {
            this.$('danmaku-feed').replaceChildren(this.emptyFeed.cloneNode(true));
            this.$('feed-count').textContent = '0';
        });
        this.$('test-idle-btn').addEventListener('click', () => this.sendTestEvent({type: 'idle_test'}));
        this.$('copy-output-btn').addEventListener('click', async () => {
            const url = `${location.origin}/?overlay=1&controls=0`;
            try { await navigator.clipboard.writeText(url); this.toast('OBS 地址已复制 · 建议 1920 × 1080'); }
            catch { this.toast(`OBS source: ${url}`); }
        });
        this.$('sound-btn').addEventListener('click', () => {
            this.sound = !this.sound;
            this.$('stage-preview').contentWindow.postMessage({type: 'preview_mute', muted: !this.sound}, location.origin);
            this.$('sound-btn').setAttribute('aria-pressed', String(this.sound));
            this.$('sound-btn').querySelector('span').textContent = this.sound ? 'Sound on' : 'Sound off';
            this.$('sound-btn').title = this.sound ? '关闭预览声音' : '开启预览声音';
        });
        document.querySelectorAll('[data-renderer]').forEach(button => button.addEventListener('click', () => {
            document.querySelectorAll('[data-renderer]').forEach(item => {
                item.classList.toggle('selected', item === button);
                item.setAttribute('aria-pressed', String(item === button));
            });
            this.$('renderer-tag').textContent = 'Loading…';
            this.$('stage-preview').src = `/?preview=1&overlay=1&muted=${this.sound ? 0 : 1}&renderer=${button.dataset.renderer}`;
        }));
        window.addEventListener('message', event => {
            if (event.origin !== location.origin || event.source !== this.$('stage-preview').contentWindow) return;
            if (event.data?.type === 'renderer_ready') {
                this.$('renderer-tag').textContent = event.data.renderer === 'sprite' ? (event.data.rig?'2D · RIGGED':'2D · LITE') : event.data.renderer.toUpperCase();
                document.querySelectorAll('[data-renderer]').forEach(button => {
                    const selected = button.dataset.renderer === event.data.renderer;
                    button.classList.toggle('selected', selected); button.setAttribute('aria-pressed', String(selected));
                });
            }
            if (event.data?.type === 'audio_blocked') this.toast('浏览器阻止自动播放，请在预览画面内点击一次');
        });
        for (const name of ['guide', 'reference']) this.$(`${name}-btn`).addEventListener('click', () => this.$(`${name}-dialog`).showModal());
        document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
        document.querySelectorAll('.nav-link').forEach(link => link.addEventListener('click', () => {
            document.querySelectorAll('.nav-link').forEach(item => item.classList.toggle('active', item === link));
        }));
        window.addEventListener('pagehide', () => {
            this.closed = true; clearTimeout(this.reconnectTimer); clearInterval(this.pingTimer); this.ws?.close();
        });
        window.addEventListener('pageshow', event => {
            if (event.persisted) { this.closed = false; this.connectWebSocket(); }
        });
    }

    async loadConfig() {
        try {
            const response = await fetch('/api/config');
            if (!response.ok) throw new Error('Configuration unavailable');
            this.config = await response.json();
            const cfg = this.config;
            this.$('engine-provider').textContent = cfg.provider === 'mock' ? 'Offline mock' : cfg.provider === 'ollama' ? 'Local Ollama' : cfg.provider === 'glm' ? 'GLM' : 'Cloud API';
            this.$('engine-note').textContent = cfg.provider === 'mock' ? 'Demo replies · no model required' : cfg.model;
            if (cfg.api_key_required && !cfg.api_key_configured) {
                this.$('engine-note').textContent = 'Open API connections to add a key';
                this.$('response-text').textContent = '请打开 API connections · 接口设置，填写文字 API 密钥并保存。';
                this.$('response-route').textContent = 'API key required';
            }
            this.$('character-name').textContent = cfg.character;
            this.$('platform-value').textContent = cfg.platform === 'bilibili' ? `Bilibili · ${cfg.room_id}` : 'Local test room';
            this.$('feed-source').textContent = cfg.platform === 'bilibili' ? 'Bilibili room' : 'Test room';
            this.$('window-value').textContent = `${cfg.filter_window}s`;
            this.$('voice-value').textContent = cfg.tts_engine === 'none' ? 'Offline demo · voice disabled' : `${cfg.tts_engine} / ${cfg.voice}`;
            this.$('danmaku-input').maxLength = cfg.max_length;
            this.updateCharCount();
            this.$('action-buttons').replaceChildren();
            for (const name of cfg.actions || []) {
                const button = document.createElement('button'); button.className = 'action-btn'; button.title = name;
                button.dataset.action = name;
                button.setAttribute('aria-pressed', 'false');
                const icon = document.createElement('span'); icon.className = 'action-icon'; icon.setAttribute('aria-hidden', 'true');
                icon.textContent = this.actions[name]?.[0] || '✧';
                const label = document.createElement('span'); label.textContent = this.actions[name]?.[1] || name;
                button.append(icon, label);
                button.addEventListener('click', async () => {
                    if (await this.sendTestEvent({type: 'action_test', action: name})) {
                        this.toast(`动作已发送 · ${label.textContent}`);
                    }
                });
                this.$('action-buttons').append(button);
            }
        } catch (error) { this.toast('配置读取失败，恢复连接后自动重试'); this.configLoaded = false; return; }
        this.configLoaded = true;
    }

    connectWebSocket() {
        if (this.closed) return;
        clearTimeout(this.reconnectTimer);
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${protocol}//${location.host}/ws`);
        this.ws.onopen = () => {
            this.connected = true; this.retry = 0;
            this.$('ws-dot').classList.add('connected'); this.$('ws-text').textContent = 'Local server connected';
            this.$('stage-state').textContent = 'Ready for a little magic';
            if (!this.configLoaded) this.loadConfig();
            clearInterval(this.pingTimer);
            this.pingTimer = setInterval(() => { if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({type: 'ping'})); }, 20000);
        };
        this.ws.onmessage = event => {
            try { this.handleMessage(JSON.parse(event.data)); }
            catch (error) { console.warn('Invalid studio event', error); }
        };
        this.ws.onclose = () => {
            this.connected = false; clearInterval(this.pingTimer);
            this.$('ws-dot').classList.remove('connected'); this.$('ws-text').textContent = 'Reconnecting…';
            this.$('pipeline-state').textContent = 'Disconnected';
            this.$('queue-count').textContent = '—'; this.$('cooldown-timer').textContent = '—';
            this.$('stage-state').textContent = 'Waiting for local server';
            this.$('platform-value').textContent = 'Status unavailable';
            this.reconnectTimer = setTimeout(() => this.connectWebSocket(), Math.min(15000, 1000 * 2 ** this.retry++));
        };
        this.ws.onerror = () => this.ws.close();
    }

    handleMessage(data) {
        if(data.modules)window.dispatchEvent(new Event('studio-modules-changed'));
        switch (data.type) {
            case 'module_notice': this.toast(data.message); break;
            case 'module_settings_updated': window.studioControls?.handleProfileEvent(data); break;
            case 'settings_updated': this.loadConfig(); window.studioControls?.handleProfileEvent(data); break;
            case 'api_connections_updated': this.loadConfig(); window.apiControls?.refreshSummary(); break;
            case 'profiles_updated': window.studioControls?.handleProfileEvent(data); break;
            case 'status': this.updateStatus(data); window.studioControls?.handleProfileEvent(data); break;
            case 'danmaku': this.addToFeed(data.user_name, data.content, false, data.timestamp); break;
            case 'thinking_start':
                this.$('response-text').textContent = '让我想一想…'; this.$('response-text').classList.add('thinking');
                this.$('pipeline-state').textContent = 'Thinking'; this.$('response-route').textContent = 'Generating a reply'; break;
            case 'thinking_end': this.$('response-text').classList.remove('thinking'); break;
            case 'response_start':
                this.$('response-text').textContent = ''; this.$('response-text').classList.remove('thinking');
                this.$('pipeline-state').textContent = 'Responding'; this.setEmotion(data.emotion); break;
            case 'response_chunk': this.$('response-text').textContent += data.text || ''; break;
            case 'response_end':
                if (data.blocked) { this.moderate(); break; }
                this.completeResponse(data.full_text, data.emotion, data.timestamp); break;
            case 'response': this.completeResponse(data.text, data.emotion, data.timestamp); break;
            case 'response_error':
                this.$('response-text').classList.remove('thinking'); this.$('response-text').textContent = '这次没有收到回复，请稍后重试。';
                this.$('response-route').textContent = 'Reply failed'; this.toast(data.message || '回复失败，请检查模型连接'); break;
            case 'moderation': this.moderate(); break;
            case 'tts_error': this.toast('语音服务暂不可用，回复已显示为文字'); break;
            case 'reply_dropped': this.toast('当前回复繁忙，这条互动已超时'); break;
            case 'idle_action': this.showAction(data.action); break;
        }
    }

    completeResponse(text, emotion, timestamp) {
        this.$('response-text').classList.remove('thinking');
        if (text) { this.$('response-text').textContent = text; this.addToFeed(this.config.character || 'Astra', text, true, timestamp); }
        this.setEmotion(emotion); this.$('response-route').textContent = 'Reply delivered';
        this.$('stage-state').textContent = 'Connected · interacting';
    }
    showAction(action = {}) {
        clearTimeout(this.actionTimer);
        const name = action.name || CharacterMotion.aliases[action.sprite_motion] || action.sprite_motion;
        document.querySelectorAll('.action-btn').forEach(button => {
            const selected = button.dataset.action === name;
            button.classList.toggle('selected', selected);
            button.setAttribute('aria-pressed', String(selected));
        });
        this.$('stage-state').textContent = `${this.config?.character || 'Astra'} · ${this.actions[name]?.[1] || 'Idle'}`;
        const duration = CharacterMotion.durations[name];
        if (duration) this.actionTimer = setTimeout(() => {
            document.querySelectorAll('.action-btn').forEach(button => {
                button.classList.remove('selected'); button.setAttribute('aria-pressed', 'false');
            });
            this.$('stage-state').textContent = 'Ready for a little magic';
        }, duration * 1000 + 300);
    }
    moderate() {
        this.$('response-text').classList.remove('thinking'); this.$('response-text').textContent = '该内容未播出。';
        this.$('response-route').textContent = 'Content filtered';
    }
    setEmotion(emotion = 'normal') {
        const labels = {normal: 'Neutral', happy: 'Happy', sad: 'Sad', angry: 'Angry', surprised: 'Surprised', thinking: 'Thinking'};
        this.$('response-emotion').textContent = labels[emotion] || emotion;
    }
    updateStatus(data) {
        this.$('queue-count').textContent = data.queue_count;
        this.$('cooldown-timer').textContent = `${data.cooldown_remaining.toFixed(1)}s`;
        this.$('idle-timer').textContent = `${data.idle_seconds}s`;
        this.$('dropped-count').textContent = data.dropped;
        this.$('pipeline-state').textContent = data.singing ? 'Singing' : this.config.api_key_required && !this.config.api_key_configured ? 'Add API key' : !data.healthy ? 'Degraded' : data.replying ? 'Responding' : data.cooldown_remaining > 0 ? 'Cooling down' : 'Ready';
        if(data.active_module&&data.active_module!=='chat')this.$('pipeline-state').textContent=data.active_module;
        window.StudioModules?.setStatus('chat',data.active_module&&data.active_module!=='chat'?'Waiting for stage':data.replying?'Replying':'Ready');
        window.StudioModules?.setStatus('singing',data.singing?'♫ Singing · 演唱中':'Library ready · 歌库待命');
        if(data.active_module==='story')window.StudioModules?.setStatus('story','☾ Telling a story · 正在讲故事');
        this.$('reply-count').replaceChildren(document.createTextNode(`${data.responses} `));
        const unit = document.createElement('em'); unit.textContent = 'replies'; this.$('reply-count').append(unit);
        this.$('template-count').textContent = data.templates;
        const latency = this.config.tts_engine === 'none' ? data.first_text_ms : data.first_audio_ms;
        this.$('latency-value').textContent = latency == null ? '—' : `${(latency / 1000).toFixed(2)} s`;
        this.$('platform-value').textContent = this.config.platform === 'bilibili' ? `Bilibili · ${data.healthy ? 'connected' : 'reconnecting'}` : 'Local test room';
    }
    updateCharCount() { this.$('char-count').textContent = `${this.$('danmaku-input').value.length} / ${this.$('danmaku-input').maxLength}`; }

    async post(url, data) {
        const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data), signal: AbortSignal.timeout(10000)});
        if (!response.ok) {
            let detail; try { detail = (await response.json()).detail; } catch {}
            throw new Error(typeof detail === 'string' ? detail : `请求失败 (${response.status})`);
        }
        return response.json();
    }
    async sendDanmaku() {
        if (this.pending) return;
        const input = this.$('danmaku-input'), content = input.value.trim();
        if (!content) return;
        if (content.length < (this.config.min_length || 2)) { this.toast(`请输入至少 ${this.config.min_length || 2} 个字`); return; }
        this.pending = true; this.$('send-btn').disabled = true;
        try {
            await this.post('/api/danmaku', {content, user_name: this.$('user-name').value.trim() || '控制台用户'});
            if (input.value.trim() === content) input.value = '';
            this.updateCharCount(); input.focus();
            this.toast('弹幕已加入队列');
        } catch (error) { this.toast(error.name === 'TimeoutError' ? '发送超时，请检查本地服务' : error.message); }
        finally { this.pending = false; this.$('send-btn').disabled = false; }
    }
    async sendTestEvent(data) {
        try { await this.post('/api/test-event', data); return true; }
        catch (error) { this.toast(error.message); return false; }
    }
    addToFeed(name, content, isAI, timestamp) {
        this.$('feed-empty')?.remove();
        const item = document.createElement('div'); item.className = `danmaku-item${isAI ? ' ai' : ''}`;
        const avatar = document.createElement('span'); avatar.className = 'feed-avatar'; avatar.textContent = isAI ? '✧' : (name || 'U').slice(0, 1);
        const body = document.createElement('div'); body.className = 'feed-body';
        const user = document.createElement('div'); user.className = 'danmaku-user'; user.textContent = name || '观众';
        const time = document.createElement('time'), date = timestamp ? new Date(timestamp * 1000) : new Date();
        time.dateTime = date.toISOString(); time.textContent = date.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'});
        const text = document.createElement('div'); text.className = 'danmaku-content'; text.textContent = content;
        user.append(time); body.append(user, text); item.append(avatar, body); this.$('danmaku-feed').append(item);
        while (this.$('danmaku-feed').children.length > 50) this.$('danmaku-feed').firstChild.remove();
        this.$('feed-count').textContent = this.$('danmaku-feed').children.length;
        this.$('danmaku-feed').scrollTop = this.$('danmaku-feed').scrollHeight;
    }
    toast(message) {
        clearTimeout(this.toastTimer); this.$('toast').textContent = message; this.$('toast').classList.add('visible');
        this.toastTimer = setTimeout(() => this.$('toast').classList.remove('visible'), 4000);
    }
}
window.addEventListener('DOMContentLoaded', () => { window.controlPanel = new ControlPanel(); });
