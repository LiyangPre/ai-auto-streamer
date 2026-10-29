const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

const tick = () => new Promise(resolve => setImmediate(resolve));
const clone = value => JSON.parse(JSON.stringify(value));

function savedSettings() {
    return {
        revision: 7,
        text: {
            provider: 'glm', base_url: 'https://open.bigmodel.cn/api/paas/v4',
            model: 'glm-4.7-flash', network_mode: 'direct', enable_thinking: false,
            fallback_models: ['glm-4-flash-250414', 'glm-4-flashx-250414', 'glm-4.7-flashx'],
            fallback_attempt_timeout: 3, fallback_total_timeout: 20, fallback_cooldown: 90,
            delay_notice_enabled: true, delay_notice_after: 3, delay_notice_cooldown: 45,
        },
        mimo: {base_url: 'https://api.xiaomimimo.com/v1', network_mode: 'auto'},
        configured: {text: true, 'mimo-tts': true, 'glm-tts': true, minimax: false},
        active_tts: 'mimo-tts', routes: {text: 'direct', mimo: 'windows_proxy'},
        glm_default_order: ['glm-4.7-flash', 'glm-4-flash-250414', 'glm-4-flashx-250414',
            'glm-4-air-250414', 'glm-4.5-air', 'glm-4.7-flashx'],
        glm_catalog: [
            {id: 'glm-4.7-flash', input_price: 0, output_price: 0, total_b: 30, active_b: 3},
            {id: 'glm-4.7-flashx', input_price: 0.5, output_price: 3, total_b: null, active_b: null},
        ],
        text_runtime: {last_model: 'glm-4-flash-250414', cooldowns: {'glm-4.7-flash': 21.4}},
    };
}

function fixture(settings = savedSettings()) {
    const nodes = new Map(), requests = [], created = [];
    let current = clone(settings), handler;
    class Element {
        constructor(tagName = 'div', id = '') {
            this.tagName = tagName.toLowerCase(); this.id = id; this.children = [];
            this._text = ''; this._value = ''; this.hidden = false; this.disabled = false;
            this.checked = false; this.listeners = new Map();
            const names = new Set();
            this.classList = {
                add: name => names.add(name), remove: name => names.delete(name),
                contains: name => names.has(name),
                toggle(name, active) {
                    active = active === undefined ? !names.has(name) : !!active;
                    if (active) names.add(name); else names.delete(name);
                    return active;
                },
            };
        }
        set value(value) { this._value = String(value); }
        get value() { return this._value; }
        set textContent(value) { this._text = String(value); this.children = []; }
        get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
        set innerHTML(value) { throw Error('API content must be rendered as literal text'); }
        appendChild(child) { this.children.push(child); return child; }
        replaceChildren(...children) { this.children = [...children]; this._text = ''; }
        addEventListener(type, callback) {
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(callback);
        }
        async dispatch(type) {
            return Promise.all((this.listeners.get(type) || []).map(callback => callback({preventDefault() {}})));
        }
        showModal() { this.open = true; }
        close() { this.open = false; return this.dispatch('close'); }
        querySelectorAll() {
            return [...nodes.values()].filter(node => node.id.startsWith('api-')
                && ['input', 'select', 'textarea', 'button'].includes(node.tagName)
                && node.id !== 'api-close');
        }
    }
    // Use IDs from the actual HTML, so missing production controls cannot be
    // hidden by a fake DOM that creates arbitrary requested elements.
    const html = fs.readFileSync('frontend/control.html', 'utf8');
    for (const match of html.matchAll(/<([a-z][a-z0-9-]*)\b[^>]*\bid="([^"]+)"[^>]*>/gi)) {
        if (!nodes.has(match[2])) nodes.set(match[2], new Element(match[1], match[2]));
    }
    const get = id => {
        assert.ok(nodes.has(id), `Missing actual controller element: ${id}`);
        return nodes.get(id);
    };
    const sandbox = {
        window: {addEventListener() {}},
        document: {
            getElementById: get,
            createElement(tag) { const element = new Element(tag); created.push(element); return element; },
        },
        AbortSignal: {timeout: milliseconds => ({timeout: milliseconds})},
        fetch: async (url, options) => {
            const call = {url, ...options, body: options.body ? JSON.parse(options.body) : undefined};
            requests.push(call);
            if (handler) return handler(call);
            return {ok: true, json: async () => clone(current)};
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync('frontend/static/api-controls.js', 'utf8') + '\nthis.Controls=APIControls;', sandbox);
    const controller = new sandbox.Controls();
    controller.fill(clone(current));
    return {controller, nodes, get, requests, created, sandbox,
        setHandler: value => handler = value, setSaved: value => current = clone(value)};
}

test('saved fallback order, time budgets and wait-notice preferences round-trip without inventing keys', () => {
    const saved = savedSettings(); saved.text.delay_notice_enabled = false;
    saved.text.fallback_attempt_timeout = 5; saved.text.fallback_total_timeout = 24;
    const f = fixture(saved), body = clone(f.controller.collect());
    assert.deepEqual(body.text, saved.text);
    assert.equal(body.revision, saved.revision);
    assert.deepEqual(body.mimo, saved.mimo);
    assert.deepEqual(body.keys, {}, 'saved key presence must not create a replacement secret');
    assert.equal(f.get('api-text-fallbacks').value, saved.text.fallback_models.join('\n'));
    assert.equal(f.get('api-delay-notice').checked, false);
});

test('edited chain keeps literal operator order, timeouts and thinking disabled', () => {
    const f = fixture();
    f.get('api-text-fallbacks').value = 'glm-4.5-air\n glm-4-flash-250414，glm-4.7-flashx';
    f.get('api-text-attempt-timeout').value = '4'; f.get('api-text-total-timeout').value = '18';
    f.get('api-delay-notice').checked = false;
    const body = clone(f.controller.collect());
    assert.deepEqual(body.text.fallback_models, ['glm-4.5-air', 'glm-4-flash-250414', 'glm-4.7-flashx']);
    assert.equal(body.text.fallback_attempt_timeout, 4); assert.equal(body.text.fallback_total_timeout, 18);
    assert.equal(body.text.delay_notice_enabled, false); assert.equal(body.text.enable_thinking, false);
    assert.equal(body.text.fallback_cooldown, 90);
    assert.equal(body.text.delay_notice_after, 3); assert.equal(body.text.delay_notice_cooldown, 45);
});

test('economical preset uses server order with busy 4.7-FlashX last and resets wait budgets', async () => {
    const saved = savedSettings(), f = fixture(saved);
    f.get('api-text-attempt-timeout').value = 12; f.get('api-text-total-timeout').value = 60;
    await f.get('api-fallback-preset').dispatch('click');
    const text = clone(f.controller.collect()).text;
    assert.deepEqual([text.model, ...text.fallback_models], saved.glm_default_order);
    assert.equal(text.fallback_models.at(-1), 'glm-4.7-flashx');
    assert.equal(text.fallback_models.includes('glm-4.5-flash'), false, 'redirect alias is not an independent fallback');
    assert.equal(text.fallback_attempt_timeout, 3); assert.equal(text.fallback_total_timeout, 20);
});

test('changing to another provider hides GLM controls and sends no GLM backup models', async () => {
    const f = fixture();
    f.get('api-text-key').value = 'previous-provider-key';
    f.get('api-text-provider').value = 'ollama';
    await f.get('api-text-provider').dispatch('change');
    const body = clone(f.controller.collect());
    assert.equal(f.get('api-glm-fallback-fields').hidden, true);
    assert.deepEqual(body.text.fallback_models, []); assert.equal(body.text.provider, 'ollama');
    assert.equal(body.text.base_url, 'http://127.0.0.1:11434/v1');
    assert.equal(body.text.enable_thinking, false); assert.equal(f.get('api-text-key').value, '');
    f.get('api-text-provider').value = 'glm'; await f.get('api-text-provider').dispatch('change');
    assert.equal(f.get('api-glm-fallback-fields').hidden, false);
    assert.equal(f.get('api-text-model').value, 'glm-4.7-flash');
});

test('price/model metadata stays literal text and commercial sizes stay unknown', () => {
    const f = fixture();
    const attack = '<img src=x onerror=alert(1)>';
    f.controller.renderCatalog([
        {id: attack, input_price: 0.5, output_price: 3, total_b: null, active_b: null},
        {id: 'glm-4.7-flash', input_price: 0, output_price: 0, total_b: 30, active_b: 3},
    ]);
    const table = f.get('api-glm-catalog').children[0];
    assert.equal(table.tagName, 'table'); assert.equal(table.children[1].children[0].textContent, attack);
    assert.equal(table.children[1].children[1].textContent, '0.5 / 3');
    assert.equal(table.children[1].children[2].textContent, '未公开');
    assert.equal(table.children[2].children[2].textContent, '30B / 3B');
    assert.ok(f.created.every(node => ['table', 'tr', 'td'].includes(node.tagName)));
});

test('runtime summary identifies the successful backup and the cooling primary', () => {
    const f = fixture();
    assert.match(f.get('api-summary').textContent, /^glm-4-flash-250414 · Auto fallback/);
    assert.match(f.get('api-summary').textContent, /Thinking off/);
    assert.match(f.get('api-fallback-runtime').textContent, /最近成功：glm-4-flash-250414/);
    assert.match(f.get('api-fallback-runtime').textContent, /glm-4\.7-flash 冷却 22s/);
});

test('successful API save retains edits, clears typed secrets and never puts them in visible results', async () => {
    const f = fixture(); await tick();
    f.get('api-text-key').value = 'private-new-text-key'; f.get('api-tts-key').value = 'private-new-tts-key';
    f.get('api-text-fallbacks').value = 'glm-4.5-air\nglm-4.7-flashx';
    let sent;
    f.setHandler(call => {
        assert.equal(call.url, '/api/connections'); assert.equal(call.method, 'POST');
        assert.equal(call.headers['X-Studio-API'], '1'); assert.equal(call.cache, 'no-store');
        sent = call.body;
        const response = savedSettings(); response.revision = 8; response.text = clone(sent.text);
        return {ok: true, json: async () => response};
    });
    await f.controller.save();
    assert.equal(sent.keys.text, 'private-new-text-key'); assert.equal(sent.keys['mimo-tts'], 'private-new-tts-key');
    assert.deepEqual(clone(f.controller.collect()).text.fallback_models, ['glm-4.5-air', 'glm-4.7-flashx']);
    assert.equal(f.controller.data.revision, 8);
    assert.equal(f.get('api-text-key').value, ''); assert.equal(f.get('api-tts-key').value, '');
    assert.ok([...f.nodes.values()].every(node => !node.textContent.includes('private-new-')));
    assert.equal(f.get('api-text-fallbacks').disabled, false);
});

test('connection test reports actual backup, first-text latency and failed attempt without exposing draft keys', async () => {
    const f = fixture(); await tick();
    f.get('api-text-key').value = 'private-draft-key';
    let sent;
    f.setHandler(call => {
        sent = call;
        return {ok: true, json: async () => ({ok: true, target: 'text', message: '文本连接成功',
            model: 'glm-4-flash-250414', first_text_ms: 430, elapsed_ms: 3590, route: 'direct',
            attempts: [{model: 'glm-4.7-flash', ok: false, code: 'timeout'},
                {model: 'glm-4-flash-250414', ok: true}]})};
    });
    await f.controller.test('text');
    assert.equal(sent.url, '/api/connections/test'); assert.equal(sent.body.target, 'text');
    assert.equal(sent.body.keys.text, 'private-draft-key');
    const result = f.get('api-text-result');
    assert.match(result.textContent, /glm-4-flash-250414/);
    assert.match(result.textContent, /首字 0\.43s/); assert.match(result.textContent, /总计 3\.59s/);
    assert.match(result.textContent, /glm-4\.7-flash: timeout → glm-4-flash-250414: ✓/);
    assert.doesNotMatch(result.textContent, /private-draft-key/);
    assert.equal(result.classList.contains('error'), false);
});

test('all-model failure stays visibly failed rather than showing a synthetic success', async () => {
    const f = fixture(); await tick();
    f.setHandler(() => ({ok: true, json: async () => ({ok: false, code: 'models_cooling_down',
        message: 'All configured GLM models are cooling down.', elapsed_ms: 2, route: 'direct'})}));
    await f.controller.test('text');
    assert.equal(f.get('api-text-result').classList.contains('error'), true);
    assert.match(f.get('api-text-result').textContent, /^All configured GLM/);
    assert.doesNotMatch(f.get('api-text-result').textContent, /✓/);
});

test('closing while a probe is pending clears secrets and ignores its stale result', async () => {
    const f = fixture(); await tick();
    let finish;
    f.setHandler(() => new Promise(resolve => { finish = resolve; }));
    f.get('api-text-key').value = 'temporary-draft-key';
    const probe = f.controller.test('text');
    assert.equal(f.get('api-text-fallbacks').disabled, true);
    await f.get('api-dialog').close();
    assert.equal(f.get('api-text-key').value, '');
    finish({ok: true, json: async () => ({ok: true, message: 'STALE SUCCESS'})});
    await probe;
    assert.doesNotMatch(f.get('api-text-result').textContent, /STALE SUCCESS/);
    assert.equal(f.get('api-text-fallbacks').disabled, false);
});

function stageErrorFixture() {
    const sandbox = {window: {addEventListener() {}}};
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync('frontend/static/app.js', 'utf8') + '\nthis.Client=AIStreamerClient;', sandbox);
    const client = Object.create(sandbox.Client.prototype);
    let stops = 0, finishes = 0;
    const names = new Set(['thinking']);
    const element = {textContent: '不好意思，我的思考模型正在检修中，稍等一下下～',
        classList: {remove: name => names.delete(name), add: name => names.add(name)}};
    Object.assign(client, {elements: {responseText: element},
        stopAudio: () => { stops++; client.audioQueue = []; },
        subtitles: {finish() { finishes++; }},
        audioQueue: [{url: '/cached-wait-notice.mp3'}], modules: null});
    return {client, element, stops: () => stops, finishes: () => finishes};
}

test('an LLM failure after spoken wait notice preserves the queued apology and caption', () => {
    const f = stageErrorFixture(), caption = f.element.textContent;
    f.client.handleMessage({type: 'response_error', reason: 'llm_failed', notice_spoken: true});
    assert.equal(f.stops(), 0); assert.equal(f.client.audioQueue.length, 1);
    assert.equal(f.element.textContent, caption); assert.equal(f.finishes(), 1);
});

test('normal reply errors still clear old audio and show a readable failure', () => {
    const f = stageErrorFixture();
    f.client.handleMessage({type: 'response_error', reason: 'llm_failed', notice_spoken: false});
    assert.equal(f.stops(), 1); assert.equal(f.client.audioQueue.length, 0);
    assert.equal(f.element.textContent, '这次没有收到回复'); assert.equal(f.finishes(), 1);
});

function queuedSpeechFixture() {
    const created = [];
    class Audio {
        constructor(url) { this.url = url; created.push(this); }
        async play() {}
        pause() {}
    }
    class Context {
        state = 'running'; destination = {};
        createAnalyser() { return {frequencyBinCount: 128, connect() {}}; }
        createMediaElementSource() { return {connect() {}, disconnect() {}}; }
    }
    const sandbox = {window: {addEventListener() {}, AudioContext: Context}, Audio,
        Uint8Array, Float32Array, DOMException, setTimeout: () => 0, clearTimeout() {},
        console: {error() {}}};
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync('frontend/static/stage-presentation.js', 'utf8'), sandbox);
    vm.runInContext(fs.readFileSync('frontend/static/app.js', 'utf8') + '\nthis.Client=AIStreamerClient;', sandbox);
    const classes = new Set(['empty']), element = {textContent: '',
        classList: {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)}};
    const client = Object.create(sandbox.Client.prototype);
    Object.assign(client, {audioQueue: [], audioPlaying: false, audioGeneration: 0,
        audioContext: null, analyser: null, renderer: null, muted: false,
        isSpeaking: false, isPreview: false, voiceEnabled: true, modules: null,
        animator: {setTarget() {}}, elements: {responseText: element},
        updateEmotion() {}, setTargetAction() {},
        subtitles: new sandbox.window.StageSubtitles(element, 2)});
    return {client, created, element, classes};
}

test('wait notice and recovered answer share one real audio FIFO with captions following playback', async () => {
    const f = queuedSpeechFixture();
    f.client.handleMessage({type: 'audio_segment', source: 'delay_notice', index: -1,
        text: '稍等一下下～', emotion: 'shy', audio_url: '/wait.mp3'});
    await tick();
    assert.equal(f.created.length, 1); assert.equal(f.element.textContent, '');
    f.created[0].onplaying(); assert.equal(f.element.textContent, '稍等一下下～');
    f.client.handleMessage({type: 'response_start', emotion: 'happy'});
    f.client.handleMessage({type: 'response_chunk', text: '你好呀，欢迎回来！'});
    f.client.handleMessage({type: 'audio_segment', index: 0, text: '你好呀，欢迎回来！',
        emotion: 'happy', audio_url: '/answer.mp3'});
    await tick();
    assert.equal(f.created.length, 1, 'reply must not overlap the notice');
    assert.equal(f.element.textContent, '稍等一下下～');
    assert.equal(f.classes.has('empty'), false);
    f.created[0].onended(); await tick();
    assert.equal(f.created.length, 2); assert.equal(f.created[1].url, '/answer.mp3');
    assert.equal(f.element.textContent, '稍等一下下～', 'new caption waits for actual voice');
    f.created[1].onplaying(); assert.equal(f.element.textContent, '你好呀，欢迎回来！');
    f.created[1].onended(); await tick(); assert.equal(f.client.audioPlaying, false);
});

test('all-model error lets the active notice finish naturally without interrupting audio', async () => {
    const f = queuedSpeechFixture();
    f.client.handleMessage({type: 'audio_segment', source: 'delay_notice', index: -1,
        text: '不好意思，稍等一下下～', audio_url: '/wait.mp3'});
    await tick(); f.created[0].onplaying();
    const audio = f.client.currentAudio;
    f.client.handleMessage({type: 'response_error', notice_spoken: true});
    assert.equal(f.client.currentAudio, audio); assert.equal(f.client.audioPlaying, true);
    assert.equal(f.element.textContent, '不好意思，稍等一下下～');
    f.created[0].onended(); await tick(); assert.equal(f.client.audioPlaying, false);
});
