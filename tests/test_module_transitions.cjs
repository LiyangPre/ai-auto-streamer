const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

async function fixture({moduleId='singing',preview=false,muted=false,now=100}={}){
    const players=[],spoken=[],subtitles=[],requests=[],emotions=[];let clock=now;
    const box={window:{},StudioModules:{script:async()=>{}},Date:{now:()=>clock*1000},setTimeout:()=>1,clearTimeout(){},
        fetch:(...args)=>{requests.push(args);return Promise.resolve({ok:true});}};
    box.SingingPlayer=class{
        constructor(options){this.options=options;this.loads=[];this.vocalPlaying=false;this.timelinePlaying=false;this.position=0;players.push(this);}
        load(value){this.stop();this.performance=value;this.loads.push(value);if(value)this.options.onStart(value);}
        stop(reason='stopped'){const p=this.performance;this.performance=null;this.vocalPlaying=false;if(p)this.options.onStop({reason,performance:p});}
        pause(){this.paused=true;this.vocalPlaying=false;}
        resume(value){this.performance=value;this.paused=false;this.options.onStart(value);}
        unlock(){}setMuted(value){this.muted=value;}dispose(){this.stop();}
    };
    vm.createContext(box);vm.runInContext(fs.readFileSync('frontend/static/modules/performance-output.js','utf8'),box);
    const client={isPreview:preview,muted,stopAudio(){},playAudio:(...args)=>spoken.push(args),
        modules:{presentation:()=>({expression:'neutral',pose:'idle',motion_strength:.3})},
        expressionController:{setModule(){},setExpression:(...args)=>emotions.push(args),setSpeaking(){}},
        carScene:{setSinging(){},setExpression(){}},renderer:{setAction(){},face:{setExpression(){}}},
        music:{setSpeaking(){}},subtitles:{begin(){},show:value=>subtitles.push(value),finish(){}},voiceEnvelope:{reset(){}}};
    const adapter=await box.window.createPerformanceAdapter(client,moduleId);
    const clip={id:'intro-one',text:'先唱一首给你听。',audio_url:'/audio/module_transition_'+'a'.repeat(64)+'.mp3',duration:1.2,starts_at:100.15};
    const performance={id:'take-one',song:{title:'海屿你',url:'/api/singing/audio/'+'b'.repeat(32),duration:295.5},
        starts_at:103,vocal_volume:.85,vocal_delay:0,music:'quiet',bpm:80,presentation:{pose:'idle',expression:'singing',motion_strength:.3},intro:clip};
    return {adapter,players,spoken,subtitles,requests,emotions,client,performance,clip,advance:value=>clock=value};
}

test('intro uses a separate local voice while the unchanged song clock preloads',async()=>{
    const f=await fixture();f.adapter.handle({type:'singing_start',performance:f.performance});
    const [main,intro]=f.players;assert.equal(main.loads[0],f.performance);assert.equal(main.performance.starts_at,103);
    assert.equal(intro.performance.starts_at,100.15);assert.equal(intro.performance.song.url,f.clip.audio_url);
    assert.equal(intro.performance.song.title,f.clip.text);assert.equal(intro.performance.music,'shared');assert.equal(intro.performance.vocal_volume,.85);
    assert.equal(f.subtitles.at(-1),f.clip.text);assert.equal(f.adapter.active,true);
    intro.vocalPlaying=true;assert.equal(f.adapter.voice.playing,true);assert.equal(f.requests.length,0);
    f.advance(103.08);main.timelinePlaying=true;main.vocalPlaying=true;f.adapter.frame({});
    assert.equal(intro.performance,null);assert.equal(main.performance.starts_at,103);assert.equal(f.subtitles.at(-1),'♫ 海屿你');
    assert.equal(f.emotions.at(-1)[0],'singing');assert.equal(f.spoken.length,0);
});

test('natural completion speaks its transition once through the chat audio queue and ignores stale stops',async()=>{
    const f=await fixture(),outro={...f.clip,id:'outro-one',text:'这首唱完啦，我们接着聊。'};
    f.adapter.handle({type:'singing_start',performance:f.performance});
    f.adapter.handle({type:'singing_stop',playback_id:'older-take',transition:outro});assert.equal(f.adapter.active,true);assert.equal(f.spoken.length,0);
    const stop={type:'singing_stop',playback_id:f.performance.id,transition:outro};f.adapter.handle(stop);f.adapter.handle(stop);
    assert.equal(f.adapter.active,false);assert.equal(f.players[1].performance,null);
    assert.deepEqual(f.spoken,[[outro.audio_url,outro.text,'happy']]);assert.equal(f.requests.length,0);
});

test('manual stop, silent preview, mute and invalid transition URLs never start an end line',async()=>{
    const f=await fixture();f.adapter.handle({type:'singing_start',performance:f.performance});f.adapter.handle({type:'singing_stop',playback_id:f.performance.id});
    assert.equal(f.spoken.length,0);
    for(const options of [{preview:true},{muted:true}]){
        const silent=await fixture(options);silent.adapter.handle({type:'singing_start',performance:silent.performance});
        assert.equal(silent.players[1].loads.length,0);
        silent.adapter.handle({type:'singing_stop',playback_id:silent.performance.id,transition:silent.clip});assert.equal(silent.spoken.length,0);
    }
    f.adapter.handle({type:'singing_stop',playback_id:'bad-clip',transition:{...f.clip,audio_url:'https://example.com/audio.mp3'}});assert.equal(f.spoken.length,0);
    f.adapter.setMuted(true);f.adapter.handle({type:'singing_stop',playback_id:'muted-after-start',transition:f.clip});assert.equal(f.spoken.length,0);
});

test('reconnecting restores only an unexpired intro and never replays it on duplicate snapshots',async()=>{
    const f=await fixture({moduleId:'story',now:100.8}),snapshot={module_snapshots:{story:f.performance}};
    f.adapter.handle(snapshot);assert.equal(f.players[1].loads.length,1);assert.equal(f.players[1].performance.starts_at,100.15);
    f.adapter.handle(snapshot);assert.equal(f.players[1].loads.length,1);
    const late=await fixture({moduleId:'story',now:102});late.adapter.handle({module_snapshots:{story:late.performance}});
    assert.equal(late.players[1].loads.length,0);assert.equal(late.players[0].performance.starts_at,103);
});

test('a story welcome still pauses and resumes at the existing performance clock',async()=>{
    const f=await fixture({moduleId:'story'});delete f.performance.intro;f.performance.presentation.expression='thinking';
    f.adapter.handle({type:'story_start',performance:f.performance});
    f.adapter.handle({type:'performance_welcome',module_id:'story',playback_id:f.performance.id,greeting:{...f.clip,id:'hello',text:'欢迎回来。'}});
    assert.equal(f.players[0].paused,true);assert.equal(f.players[1].performance.song.title,'欢迎回来。');
    f.players[1].stop('ended');assert.equal(f.requests.length,1);assert.match(f.requests[0][0],/\/resume-welcome$/);
    const resumed={...f.performance,starts_at:105};f.adapter.handle({type:'performance_resume',module_id:'story',performance:resumed});
    assert.equal(f.players[0].paused,false);assert.equal(f.players[0].performance.starts_at,105);assert.equal(f.emotions.at(-1)[0],'thinking');
});
