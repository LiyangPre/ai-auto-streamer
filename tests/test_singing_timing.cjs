const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

function fixture({blocked=false,defer=false}={}){
    let wall=100,nextTimer=0;
    const sources=[],contexts=[],pendingReads=[],requests=[],timers=new Map(),expressionCalls=[];
    class ClockDate extends Date{static now(){return wall*1000;}}
    class Context{
        constructor(){this.currentTime=10;this.state=blocked?'suspended':'running';this.destination={};contexts.push(this);}
        createAnalyser(){return {frequencyBinCount:512,connect(){}};}
        async resume(){}
        async decodeAudioData(){return {duration:300};}
        createGain(){return {gain:{value:0},connect(){},disconnect(){}};}
        createBufferSource(){const source={playbackRate:{value:1},loop:false,connect(){},disconnect(){},
            start(when,offset,duration){this.when=when;this.offset=offset;this.duration=duration;},stop(){this.stopped=true;}};sources.push(source);return source;}
        close(){}
    }
    const response=()=>({ok:true,headers:{get:()=>100},arrayBuffer:async()=>new ArrayBuffer(4)});
    const box={window:{AudioContext:Context},console,AbortController,Float32Array,Uint8Array,Date:ClockDate,
        fetch:(url,options)=>{requests.push({url,options});if(url.startsWith('/api/modules/'))return Promise.resolve(response());
            return defer?new Promise(resolve=>pendingReads.push(()=>resolve(response()))):Promise.resolve(response());},
        setTimeout:(fn,ms)=>{const id=++nextTimer;timers.set(id,{fn,ms,at:wall+ms/1000});return id;},clearTimeout:id=>timers.delete(id),
        StudioModules:{script:async()=>{}}};
    vm.createContext(box);
    vm.runInContext(fs.readFileSync('frontend/static/vendor/expression-engine.bundle.js','utf8'),box);
    vm.runInContext(fs.readFileSync('frontend/static/singing-player.js','utf8')+'\nthis.Player=SingingPlayer;',box);
    vm.runInContext('this.players=[]; const ActualPlayer=SingingPlayer; SingingPlayer=class extends ActualPlayer{constructor(options){super(options);players.push(this);}};',box);
    vm.runInContext(fs.readFileSync('frontend/static/modules/performance-output.js','utf8'),box);
    const controller=new box.window.CharacterExpressionController({profileId:'astra',seed:12});
    const originalSet=controller.setExpression.bind(controller);
    controller.setExpression=(name,options)=>{expressionCalls.push({name,options});return originalSet(name,options);};
    const client={isPreview:false,muted:false,expressionController:controller,stopAudio(){},
        modules:{presentation:()=>({pose:'auto',expression:'neutral',expression_enabled:true,expression_strength:.55})},
        carScene:{setSinging(){},setExpression(){}},renderer:{setAction(){},face:{setExpression(){}}},
        music:{setPerformance(){},setSpeaking(){}},subtitles:{begin(){},show(){},finish(){}},voiceEnvelope:{reset(){}}};
    const performance={id:'take1',song:{url:'/api/singing/audio/'+'a'.repeat(32),guide_url:'/api/singing/audio/'+'a'.repeat(32)+'/guide',duration:300,title:'Fixed recording'},
        starts_at:103,vocal_delay:0,vocal_volume:.85,backing_volume:0,backing_url:null,music:'quiet',bpm:90,
        presentation:{pose:'idle',expression:'auto',motion_strength:.3,expression_enabled:true,expression_strength:.55}};
    return {box,controller,client,performance,sources,contexts,requests,timers,expressionCalls,
        advanceWall:s=>{wall+=s;},releaseReads:()=>{pendingReads.splice(0).forEach(fn=>fn());}};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('a saved segment seeks mix and mouth guide together and ends at its selected boundary',async()=>{
    const f=fixture(),player=new f.box.Player({});
    const take={...f.performance,start_offset:60,play_duration:30};
    await player.load(take);
    assert.equal(f.sources.length,2);
    for(const source of f.sources){assert.equal(source.offset,60);assert.equal(source.duration,30);assert.equal(source.playbackRate.value,1);}
    assert.equal(player.performance.song.duration,300);
    player.ctx.currentTime=f.sources[0].when+10;const paused=player.position;
    player.pause();player.resume({...take,starts_at:113});await tick();
    assert.ok(Math.abs(f.sources.at(-2).offset-60-paused)<1e-9);
    assert.ok(Math.abs(f.sources.at(-2).duration-(30-paused))<1e-9);
    f.sources.at(-2).onended();assert.equal(player.performance,null);
});

test('a late output joins within the selected segment and never restarts after its end',async()=>{
    const f=fixture(),player=new f.box.Player({});f.advanceWall(13);
    await player.load({...f.performance,start_offset:100,play_duration:30});
    assert.equal(f.sources[0].offset,110);assert.equal(f.sources[0].duration,20);player.stop();
    f.advanceWall(60);const before=f.sources.length;
    await player.load({...f.performance,id:'expired',start_offset:100,play_duration:30});
    assert.equal(f.sources.length,before);assert.equal(player.performance,null);
});
async function adapterFixture(options){const f=fixture(options);f.adapter=await f.box.window.createPerformanceAdapter(f.client,'singing');return f;}
function sample(f){const params={};f.adapter.frame(params);f.controller.setSpeaking(f.adapter.voice.playing);
    params.face=f.controller.sample(.1,{mouthOpen:.7,mouthRound:.2});return params;}

test('a five-minute recording keeps its audio clock and held expression, then returns to chat on the real end',async()=>{
    const f=await adapterFixture();f.adapter.handle({type:'singing_start',performance:f.performance});await tick();
    const ctx=f.contexts[0],main=f.box.players[0];
    assert.equal(f.sources.length,2,'the full mix plays once; the guide is a silent analysis source');
    assert.ok(Math.abs(f.sources[0].when-13.08)<1e-9,'retain the existing three-second lead and 80ms scheduling cushion');
    assert.equal(f.sources[0].offset,0);assert.equal(f.sources[0].playbackRate.value,1);
    assert.equal(sample(f).moduleSway,undefined,'do not sway before scheduled sound starts');
    ctx.currentTime=13.08+12.5;
    const before=sample(f);assert.ok(Math.abs(before.moduleSway.phase-12.5*90/60*Math.PI)<1e-9);
    f.advanceWall(45);
    assert.equal(sample(f).moduleSway.phase,before.moduleSway.phase,'a wall-clock correction cannot move the beat ahead of the recording');
    for(let i=0;i<3000;i++)f.controller.sample(.1,{mouthOpen:.7,mouthRound:.2});
    assert.equal(f.controller.expression,'singing');assert.equal(f.controller.hold,Infinity);
    assert.equal(main.performance.song.duration,300);assert.equal(f.sources[0].buffer.duration,300);
    ctx.currentTime=f.sources[0].when+300;
    f.sources[0].onended();
    assert.equal(f.adapter.active,false);assert.equal(f.controller.moduleId,'chat');assert.equal(f.controller.expression,'neutral');
    assert.equal(f.controller.sample(.1,{mouthOpen:.7}).mouthOpen,0);
    const complete=f.requests.filter(row=>row.url==='/api/modules/singing/complete');
    assert.equal(complete.length,1);assert.equal(JSON.parse(complete[0].options.body).playback_id,'take1');
    assert.equal(f.requests.length,3,'expressions add no request: only mix, guide and end acknowledgement');
    f.adapter.dispose();
});

test('slow decoding and blocked autoplay stay still, then join the fixed recording at elapsed time',async()=>{
    const f=await adapterFixture({blocked:true,defer:true});
    f.adapter.handle({type:'singing_start',performance:f.performance});
    assert.equal(f.sources.length,0);assert.equal(sample(f).moduleSway,undefined);assert.equal(sample(f).face.mouthOpen,0);
    f.advanceWall(10);f.releaseReads();await tick();
    assert.equal(f.sources.length,0,'decoding does not bypass browser autoplay permission');
    assert.equal(sample(f).moduleSway,undefined);assert.equal(sample(f).face.mouthOpen,0);
    f.advanceWall(2);f.contexts[0].state='running';f.adapter.unlock();await tick();
    assert.equal(f.sources.length,2);assert.equal(f.sources[0].offset,9,'late permission resumes at elapsed position, without restarting the song');
    assert.ok(Math.abs(f.sources[0].when-10.08)<1e-9);
    f.contexts[0].currentTime=10.2;
    assert.ok(sample(f).moduleSway);assert.equal(sample(f).face.mouthOpen,.7);
    f.adapter.handle({type:'singing_start',performance:f.performance});await tick();assert.equal(f.sources.length,2,'a duplicate event cannot create overlapping recordings');
    f.adapter.dispose();
});

test('pause and mute close the mouth and stop beat motion while resume preserves the exact audio position',async()=>{
    const f=await adapterFixture();f.adapter.handle({type:'singing_start',performance:f.performance});await tick();
    const ctx=f.contexts[0],main=f.box.players[0];ctx.currentTime=30;
    const position=main.position;
    assert.ok(sample(f).moduleSway);assert.equal(sample(f).face.mouthOpen,.7);
    f.adapter.setMuted(true);
    assert.equal(sample(f).moduleSway,undefined);assert.equal(sample(f).face.mouthOpen,0);
    f.adapter.setMuted(false);assert.ok(sample(f).moduleSway);
    main.pause();f.advanceWall(20);ctx.currentTime=50;
    assert.equal(main.position,position);assert.equal(sample(f).moduleSway,undefined);assert.equal(sample(f).face.mouthOpen,0);
    main.resume({...f.performance,starts_at:123});await tick();
    assert.ok(Math.abs(f.sources.at(-2).offset-position)<1e-9);
    assert.equal(f.sources.at(-2).playbackRate.value,1);
    f.adapter.dispose();
});

test('a stopped paused performance cannot leak its saved seek offset into the next recording',async()=>{
    const f=fixture({blocked:true}),player=new f.box.Player({});
    await player.load({...f.performance,paused:true,paused_at:115});
    player.resume({...f.performance,paused:false,starts_at:100});await tick();
    assert.equal(f.sources.length,0);assert.equal(player.resumeOffset,12);
    player.stop();f.contexts[0].state='running';
    await player.load({...f.performance,id:'take2'});
    assert.equal(f.sources[0].offset,0,'a newly scheduled song begins at its own timeline');
    assert.equal(player.resumeOffset,null);player.stop();
});
