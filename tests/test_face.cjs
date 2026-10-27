const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {test}=require('node:test');
const box={window:{}};vm.createContext(box);
vm.runInContext(fs.readFileSync('frontend/static/character-motion.js','utf8')+
    fs.readFileSync('frontend/static/character-face.js','utf8')+'\nthis.Face=CharacterFace;this.Voice=VoiceEnvelope;',box);
const tone=(amplitude,dc=0)=>Float32Array.from({length:1024},(_,i)=>dc+amplitude*Math.sin(i*.3));

test('blinks fully close and reopen, with irregular natural intervals',()=>{
    const face=new box.Face(()=>.5);let frames=0,closures=0;
    for(let i=0;i<600;i++) {
        const state=face.sample(1/60);
        assert.ok(state.eyeOpen>=0&&state.eyeOpen<=1.1);
        if(state.blink>.98){frames++;closures++;}
    }
    assert.ok(frames>=2&&frames<30);assert.ok(closures>0);
    face.blink();for(let i=0;i<20;i++)face.sample(1/60);
    assert.equal(face.sample(1/60).blink,0);
});
test('expression changes blend and do not reset the automatic blink timer',()=>{
    const face=new box.Face(()=>.5),next=face.nextBlink;
    face.setExpression('shy',true);const first=face.sample(1/60);
    assert.ok(first.blush>.12&&first.blush<.5);assert.equal(face.nextBlink,next);
    for(let i=0;i<60;i++)face.sample(1/60);
    assert.ok(face.current.blush>.99);
});
test('voice envelope opens promptly and closes during syllable pauses',()=>{
    const voice=new box.Voice(),silence=tone(0),speech=tone(.09);
    assert.ok(voice.sample(speech,null,48000,1/30,true).open>.35,'attack must fit within one rendered frame');
    for(let i=0;i<9;i++)voice.sample(silence,null,48000,1/30,true);
    assert.equal(voice.open,0,'sentence-internal silence must close the lips');
    assert.equal(voice.sample(speech,null,48000,1/30,false).open,0,'paused, muted or buffering audio never animates lips');
});
test('noise and DC offset do not flap lips; quieter speech remains responsive',()=>{
    const voice=new box.Voice();
    for(let i=0;i<60;i++)assert.equal(voice.sample(tone(.003,.1),null,48000,1/30,true).open,0);
    assert.ok(voice.sample(tone(.025),null,48000,1/30,true).open>.15);
});
test('spectral balance changes roundness without random mouth shapes',()=>{
    const low=new Uint8Array(512),high=new Uint8Array(512);
    low.fill(100,4,18);high.fill(100,22,60);
    const a=new box.Voice(),b=new box.Voice();
    for(let i=0;i<30;i++){a.sample(tone(.08),low,48000,1/30,true);b.sample(tone(.08),high,48000,1/30,true);}
    assert.ok(a.round>b.round+.6);assert.equal(a.open,b.open);
});
