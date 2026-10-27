const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

function fixture({ blocked = false, holdFirst = false, holdAll = false } = {}) {
    const created = [], sources = [];
    let connections = 0;
    class FakeAudio {
        constructor(url) { assert.ok(url, 'Audio must never receive an undefined URL'); this.url = url; created.push(this); }
        async play() {
            if (blocked) throw new DOMException('gesture required', 'NotAllowedError');
            if (!holdAll && !(holdFirst && created.length === 1)) queueMicrotask(() => this.onended?.());
        }
        pause() {}
    }
    class FakeAudioContext {
        state = 'running';
        destination = {};
        createAnalyser() { return {frequencyBinCount: 128, connect() { connections++; }}; }
        async resume() {}
        createMediaElementSource() {
            const source = {connect() {}, disconnect() { this.disconnected = true; }};
            sources.push(source); return source;
        }
    }
    const sandbox = {window: {addEventListener() {}, AudioContext: FakeAudioContext}, Audio: FakeAudio,
        URLSearchParams, location: {search: '', origin: 'http://localhost'}, console: {error() {}},
        DOMException, Uint8Array, setTimeout, clearTimeout, performance};
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync('frontend/static/app.js', 'utf8') + '\nthis.Client = AIStreamerClient;', sandbox);
    const client = Object.create(sandbox.Client.prototype);
    Object.assign(client, {audioQueue: [], audioPlaying: false, audioGeneration: 0, audioContext: null,
        analyser: null, renderer: null, muted: false, isSpeaking:false, isPreview: false, animator: {setTarget() {}}});
    return {client, created, sources, connections: () => connections};
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('switching profiles stops old queued speech and keeps the shared music player',async()=>{
    const {client,created}=fixture({holdFirst:true});
    let hidden=0,sceneApplied;
    const music={setSpeaking(){},configure(scene){sceneApplied=scene;}};
    client.music=music;client.activeProfileId='astra';client.pendingCaption='Old line';
    client.subtitles={setAudioBusy(){},waitForSpeech(){hidden++;},setDuration(){},finish(){}};
    client.applyScene=scene=>music.configure(scene);
    const original=client.playAudio('/old-profile.mp3');await tick();
    client.playAudio('/old-profile-queued.mp3');
    const scene={background:'car',music_enabled:true,music_volume:.22};
    client.handleMessage({type:'settings_updated',profile_changed:true,profile_context:{active_id:'mira'},
        settings:{scene,identity:{name:'Mira'},voice:{enabled:true},actions:{intensity:.4}}});
    await original;await tick();
    assert.equal(client.activeProfileId,'mira');assert.equal(client.pendingCaption,'');
    assert.equal(client.audioQueue.length,0);assert.equal(client.music,music);assert.equal(sceneApplied,scene);
    assert.equal(hidden,1);assert.equal(created.length,1,'queued old voice must never start');
    await client.playAudio('/new-profile.mp3');assert.equal(created[1].url,'/new-profile.mp3');
});

test('reconnect snapshot corrects a missed switch while periodic status preserves playback',()=>{
    const {client}=fixture();let stops=0;
    client.stopAudio=()=>{stops++;};client.applyScene=()=>{};client.activeProfileId='astra';
    client.handleMessage({type:'status',settings:{scene:{},identity:{name:'Mira'},voice:{enabled:true},actions:{}},
        profile_context:{active_id:'mira'}});
    assert.equal(stops,1);assert.equal(client.characterName,'Mira');
    client.handleMessage({type:'status',healthy:true});assert.equal(stops,1);
});

test('interrupted audio restarts queued response without undefined URLs', async () => {
    const {client, created} = fixture({holdFirst: true});
    const original = client.playAudio('/old.mp3');
    await tick();
    client.stopAudio();
    await client.playAudio('/new.mp3');
    await original; await tick();
    assert.deepEqual(created.map(audio => audio.url), ['/old.mp3', '/new.mp3']);
    assert.equal(client.audioQueue.length, 0);
    assert.equal(client.audioPlaying, false);
});

test('sentence audio stays ordered and disconnects finished sources', async () => {
    const {client, created, sources, connections} = fixture();
    const first = client.playAudio('/one.mp3');
    const second = client.playAudio('/two.mp3');
    await Promise.all([first, second]);
    assert.deepEqual(created.map(audio => audio.url), ['/one.mp3', '/two.mp3']);
    assert.ok(sources.every(source => source.disconnected));
    assert.equal(connections(), 1, 'analyser connects to output exactly once');
});

test('speaking follows actual playing and buffering events, not the synthesis request',async()=>{
    const {client,created}=fixture({holdFirst:true});
    const states=[];client.renderer={setSpeaking(value){states.push(value);}};
    let resets=0;client.voiceEnvelope={reset(){resets++;}};
    const playback=client.playAudio('/real-voice.mp3');await tick();
    assert.equal(client.isSpeaking,false,'loading must not open the mouth');
    created[0].onplaying();assert.equal(client.isSpeaking,true);
    created[0].onwaiting();assert.equal(client.isSpeaking,false,'buffering closes the mouth');
    created[0].onplaying();assert.equal(client.isSpeaking,true);
    client.stopAudio();await playback;
    assert.equal(client.isSpeaking,false);assert.equal(states.at(-1),false);
    assert.ok(resets>=2);
});

test('autoplay denial preserves a valid segment for user gesture retry', async () => {
    const {client} = fixture({blocked: true});
    await client.playAudio('/retry.mp3');
    assert.equal(client.audioPlaying, false);
    assert.equal(client.audioQueue.length, 1);
    assert.equal(client.audioQueue[0].url, '/retry.mp3');
});

test('muted preview creates no audio and ignores missing segments', async () => {
    const {client, created} = fixture();
    client.muted = true;
    await client.playAudio('/muted.mp3');
    client.muted = false;
    await client.playAudio();
    assert.equal(created.length, 0);
    assert.equal(client.audioQueue.length, 0);
});

test('blocked playback has a bounded recent queue', async () => {
    const {client, created} = fixture({blocked: true});
    for (let index = 0; index < 1000; index++) await client.playAudio(`/${index}.mp3`);
    assert.equal(client.audioQueue.length, 16);
    assert.equal(client.audioQueue[0].url, '/984.mp3');
    assert.equal(created.length, 1, 'no repeated autoplay attempts until a gesture');
});

test('queued captions wait for their own audio playing event and remain in order',async()=>{
    const {client,created}=fixture({holdAll:true}),shown=[];
    client.subtitles={setAudioBusy(){},show(text){shown.push(text);},finish(){}};
    const one=client.playAudio('/one.mp3','First sentence');
    const two=client.playAudio('/two.mp3','Second sentence');await tick();
    assert.deepEqual(shown,[],'preparing audio must not reveal text');
    created[0].onplaying();assert.deepEqual(shown,['First sentence']);
    created[0].onended();await tick();
    assert.deepEqual(shown,['First sentence'],'next caption must wait for its own playback');
    created[1].onplaying();assert.deepEqual(shown,['First sentence','Second sentence']);
    created[1].onended();await Promise.all([one,two]);
});

test('autoplay denial keeps its caption with the audio without revealing it early',async()=>{
    const {client}=fixture({blocked:true}),shown=[];
    client.subtitles={setAudioBusy(){},show(text){shown.push(text);},finish(){}};
    await client.playAudio('/blocked.mp3','Retry with this caption');
    assert.equal(client.audioQueue[0].caption,'Retry with this caption');
    assert.deepEqual(shown,[]);
});

test('a template reply remains hidden through TTS preparation and appears on playing',async()=>{
    const {client,created}=fixture({holdFirst:true});let hidden=true,caption='';
    client.voiceEnabled=true;client.updateEmotion=()=>{};client.addDanmakuToFeed=()=>{};
    client.subtitles={waitForSpeech(){hidden=true;},begin(){hidden=false;},setAudioBusy(){},finish(){},
        show(text){hidden=false;caption=text;}};
    client.handleFullResponse({text:'The answer is ready',emotion:'normal'});
    assert.equal(hidden,true,'receiving a finished LLM answer must not show it early');
    client.handleMessage({type:'audio_segment',audio_url:'/speech.mp3',text:'Question core. The answer is ready'});
    await tick();assert.equal(hidden,true,'a TTS URL can still be loading');
    created[0].onplaying();assert.equal(hidden,false);
    assert.equal(caption,'Question core. The answer is ready');
    client.stopAudio();await tick();
});

test('muted thumbnails show ready captions without creating another audio decoder',async()=>{
    const {client,created}=fixture();client.muted=true;let shown='';
    client.subtitles={show(text){shown=text;},finish(){}};
    await client.playAudio('/ready.mp3','Ready speech');
    assert.equal(shown,'Ready speech');assert.equal(created.length,0);
});
