const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function fixture(capture) {
    const fields=new Map(),sockets=[];
    const field=id=>{if(!fields.has(id))fields.set(id,{value:'file',hidden:false,disabled:false,addEventListener(){},pause(){this.paused=true;}});return fields.get(id);};
    class Stream {constructor(tracks){this.tracks=tracks;}getAudioTracks(){return this.tracks.filter(t=>t.kind==='audio');}getVideoTracks(){return this.tracks.filter(t=>t.kind==='video');}getTracks(){return this.tracks;}}
    const track=(kind,surface='browser')=>({kind,enabled:true,stopped:false,stop(){this.stopped=true;},addEventListener(type,fn){this.ended=fn;},getSettings(){return {displaySurface:surface};}});
    class Socket {constructor(){sockets.push(this);this.readyState=1;}close(){this.closed=true;}send(){}}
    const sandbox={window:{RTCPeerConnection:true,addEventListener(){}},navigator:{mediaDevices:{getDisplayMedia:capture}},
        MediaStream:Stream,WebSocket:Socket,location:{host:'localhost:8000',protocol:'http:'},setTimeout,clearTimeout};
    vm.createContext(sandbox);vm.runInContext(fs.readFileSync('frontend/static/music-capture.js','utf8'),sandbox);
    let controls;
    const studio={$:field,outputs(){controls.update();},previewScene(){},status(message){this.message=message;}};
    controls=new sandbox.window.MusicCaptureControls(studio);
    return {controls,studio,field,sockets,Stream,track};
}

test('capture forwards audio only, preserves a low-rate permission session, and stops every track',async()=>{
    let options;const f=fixture(async value=>{options=value;return stream;});
    const audio=f.track('audio'),video=f.track('video'),stream=new f.Stream([audio,video]);
    await f.controls.choose();
    assert.equal(options.systemAudio,'exclude');assert.equal(options.audio.suppressLocalAudioPlayback,true);
    assert.equal(f.controls.bridge.stream.getTracks().length,1);assert.equal(video.enabled,false);
    assert.equal(f.field('scene-music-source').value,'system');assert.equal(f.studio.dirty,true);
    assert.equal(f.field('music-capture-live').hidden,false);assert.equal(f.field('scene-music-preview').paused,true);
    f.controls.stop();assert.ok(audio.stopped&&video.stopped);assert.equal(f.sockets[0].closed,true);
    assert.equal(f.field('music-capture-live').hidden,true);assert.equal(f.field('music-capture-stop').disabled,true);
});

test('missing audio and whole-desktop captures are rejected with all tracks released',async()=>{
    for(const monitor of [false,true]) {
        const f=fixture(async()=>stream),video=f.track('video',monitor?'monitor':'window');
        const stream=new f.Stream(monitor?[video,f.track('audio')]:[video]);
        await f.controls.choose();assert.equal(f.sockets.length,0);assert.equal(f.controls.stream,null);
        assert.ok(stream.getTracks().every(t=>t.stopped));assert.equal(f.field('music-capture-start').disabled,false);
        assert.match(f.field('music-capture-status').textContent,monitor?/整个系统混音/:/没有收到音频/);
    }
});

test('permission cancellation and stopping while the picker is pending cannot leak capture',async()=>{
    const denied=fixture(async()=>{throw Object.assign(new Error('denied'),{name:'NotAllowedError'});});
    await denied.controls.choose();assert.match(denied.field('music-capture-status').textContent,/已取消/);
    let resolve;const f=fixture(()=>new Promise(r=>resolve=r));
    const pending=f.controls.choose();f.controls.stop();
    const stream=new f.Stream([f.track('audio'),f.track('video')]);resolve(stream);await pending;
    assert.equal(f.sockets.length,0);assert.ok(stream.getTracks().every(t=>t.stopped));
});

test('browser Stop sharing tears down transport and releases remaining tracks',async()=>{
    const f=fixture(async()=>stream),stream=new f.Stream([f.track('audio'),f.track('video')]);
    await f.controls.choose();stream.getVideoTracks()[0].ended();
    assert.ok(stream.getTracks().every(t=>t.stopped));assert.equal(f.sockets[0].closed,true);
});
