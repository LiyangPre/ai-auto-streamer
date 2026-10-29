const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture({scenePreset='singing-room',transparent=false,transitions=false}={}) {
    let wall=100;
    const contexts=[],sources=[],requests=[],frames=[],raf=[],changes={scene:0,car:[],singing:[],music:[],performance:[]};
    class ClockDate extends Date {static now(){return wall*1000;}}
    class AudioContext {
        constructor(){this.currentTime=10;this.state='running';this.destination={};this.sampleRate=48000;contexts.push(this);}
        createAnalyser(){return {frequencyBinCount:512,connect(){},getFloatTimeDomainData(){},getByteFrequencyData(){}};}
        async decodeAudioData(){return {duration:60};}
        async resume(){}
        createGain(){return {gain:{value:0},connect(){},disconnect(){}};}
        createBufferSource(){const source={connect(){},disconnect(){},stop(){this.stopped=true;},
            start(when,offset){this.when=when;this.offset=offset;}};sources.push(source);return source;}
        close(){}
    }
    const context={createRadialGradient:()=>({addColorStop(){}}),fillRect(){},setTransform(){},
        clearRect(){},save(){},restore(){},drawImage(){}};
    const box={window:{addEventListener(){},removeEventListener(){},AudioContext,devicePixelRatio:1,innerWidth:1440,innerHeight:1080},
        document:{hidden:false,createElement:()=>({getContext:()=>context})},
        performance:globalThis.performance,console,Date:ClockDate,URLSearchParams,location:{search:'',origin:'http://localhost'},
        AbortController,AbortSignal,Float32Array,Uint8Array,Map,
        setTimeout:()=>1,clearTimeout(){},requestAnimationFrame:callback=>raf.push(callback),
        fetch:async(url,options)=>{requests.push({url,options});return {ok:true,headers:{get:()=>4},arrayBuffer:async()=>new ArrayBuffer(4)};}};
    vm.createContext(box);
    for(const file of ['stage-scene.js','scene-transition.js','module-host.js','singing-player.js','modules/performance-output.js','modules/singing-output.js','app.js'])
        vm.runInContext(fs.readFileSync('frontend/static/'+file,'utf8'),box);
    vm.runInContext('this.Client=AIStreamerClient;this.Scene=StageScene;this.Host=StageModuleHost;this.players=[];const ActualPlayer=SingingPlayer;SingingPlayer=class extends ActualPlayer{constructor(options){super(options);players.push(this);}};',box);
    const layer={style:{setProperty(){}},hidden:false},character={style:{}},companions={},canvas={clientWidth:1440,clientHeight:1080,getContext:()=>context};
    const scene=new box.Scene(layer,canvas,character,{transparent,companions}),actualSettings=scene.setSettings.bind(scene);
    scene.setSettings=value=>{changes.scene++;actualSettings(value);};
    const chat={pose:'auto',expression:'neutral',motion_strength:.35,scene_preset:'inherit'},
        singing={pose:'idle',expression:'singing',motion_strength:.4,scene_preset:scenePreset};
    const manifest={modules:[{id:'chat',presentation:chat},{id:'singing',presentation:singing,output:'/static/modules/singing-output.js'}]};
    box.window.StudioModules.manifest=async()=>manifest;box.window.StudioModules.script=async()=>{};
    const client=Object.create(box.Client.prototype);
    Object.assign(client,{isPreview:false,muted:false,scene,lastFrame:0,isSpeaking:false,
        carScene:{setActive(active,settings){this.active=active;changes.car.push({active,settings});},setSinging(){},setExpression(){},render(){}},
        singingScene:{setActive(active,settings){this.active=active;changes.singing.push({active,settings});},setSinging(){},setExpression(){},
            render(params,dt,hidden,timeline){frames.push({params,dt,hidden,timeline});}},
        animator:{update:()=>({})},voiceEnvelope:{sample:()=>({open:0,round:0}),reset(){}},
        renderer:{setAction(){},render(){},face:{setExpression(){}}},stopAudio(){},
        music:{configure:value=>changes.music.push(value),setPerformance:value=>changes.performance.push(value),setSpeaking(){}},
        subtitles:{setDuration(){},begin(){},show(){},finish(){},clear(){}}});
    const heldScenes=new Map();
    if(transitions){client.sceneTransition=new box.window.SceneTransition(null,{reducedMotion:true,afterPaint:()=>Promise.resolve()});
        client.prepareScene=settings=>heldScenes.get(settings.background)?.promise||Promise.resolve();}
    client.modules=new box.Host(client);await client.modules.init();
    const shared={background:'car',effect:'none',companions:true,character_x:12,character_y:4,character_scale:1.1,
        music_enabled:true,music_volume:.16,music_asset:'b'.repeat(32),music_source:'file'};
    client.applyScene(shared);if(transitions)await tick();client.startAnimationLoop();
    const performance={id:'singing-take',song:{id:'a'.repeat(32),url:'/api/singing/audio/'+'a'.repeat(32),title:'Local recording',duration:60},
        starts_at:103,vocal_delay:0,vocal_volume:.85,backing_url:null,backing_volume:0,music:'quiet',bpm:84,presentation:{...singing}};
    return {box,client,scene,layer,character,companions,shared,chat,singing,performance,changes,requests,frames,contexts,sources,
        frame(now){const callback=raf.shift();assert.ok(callback,'animation loop must schedule its next frame');callback(now);},
        wall(value){wall=value;},
        async start(){client.handleMessage({type:'singing_start',performance});await tick();},
        holdScene(background){let resolve;const promise=new Promise(done=>{resolve=done;});heldScenes.set(background,{promise,resolve});return resolve;},
        stop(){client.handleMessage({type:'singing_stop',playback_id:performance.id});},
        dispose(){client.modules.dispose();scene.dispose();client.sceneTransition?.dispose();}};
}

test('Songs selects its music room and returns to the saved car without modifying shared settings',async()=>{
    const f=await fixture();
    try {
        assert.equal(f.client.carScene.active,true);assert.equal(f.client.singingScene.active,false);
        await f.start();f.frame(40);
        assert.equal(f.scene.settings.background,'singing-room');assert.equal(f.client.singingScene.active,true);
        assert.equal(f.client.carScene.active,false);assert.equal(f.character.style.visibility,'hidden');
        assert.equal(f.companions.hidden,true,'ordinary stage dolls hide because the singing renderer owns their placement');
        assert.equal(f.scene.settings.companions,true,'the renderer still receives the saved doll visibility preference');
        assert.equal(f.client.sharedScene.background,'car');assert.equal(f.shared.background,'car');
        assert.equal(f.scene.settings.music_asset,'b'.repeat(32));assert.equal(f.scene.settings.music_volume,.16);
        f.stop();f.frame(80);
        assert.equal(f.scene.settings.background,'car');assert.equal(f.client.carScene.active,true);assert.equal(f.client.singingScene.active,false);
        assert.equal(f.client.sharedScene.character_x,12);assert.equal(f.client.sharedScene.character_scale,1.1);
        assert.equal(f.changes.music.length,1,'module scene changes must not reconfigure the shared music player');
    } finally {f.dispose();}
});

test('an explicit inherit uses the shared car throughout the song',async()=>{
    const f=await fixture({scenePreset:'inherit'});
    try {
        await f.start();f.frame(40);
        assert.equal(f.scene.settings.background,'car');assert.equal(f.client.carScene.active,true);assert.equal(f.client.singingScene.active,false);
        f.stop();f.frame(80);assert.equal(f.scene.settings.background,'car');
        assert.equal(f.singing.scene_preset,'inherit');
    } finally {f.dispose();}
});

test('forced transparent output suppresses both integrated scenes during singing and afterward',async()=>{
    const f=await fixture({transparent:true});
    try {
        await f.start();f.frame(40);
        assert.equal(f.scene.forceTransparent,true);assert.equal(f.layer.hidden,true);
        assert.equal(f.client.carScene.active,false);assert.equal(f.client.singingScene.active,false);
        assert.equal(f.scene.integrated,false);assert.equal(f.character.style.visibility,'');
        f.stop();f.frame(80);
        assert.equal(f.client.carScene.active,false);assert.equal(f.client.singingScene.active,false);
        assert.equal(f.layer.hidden,true);assert.equal(f.client.sharedScene.background,'car');
    } finally {f.dispose();}
});

test('unchanged module scene is stable across animation frames and keeps audio sources and music intact',async()=>{
    const f=await fixture();
    try {
        await f.start();f.contexts[0].currentTime=f.sources[0].when+4;f.frame(40);
        const before={scene:f.changes.scene,car:f.changes.car.length,singing:f.changes.singing.length,
            music:f.changes.music.length,performance:f.changes.performance.length,sources:f.sources.length};
        for(let i=2;i<=180;i++)f.frame(i*40);
        assert.deepEqual({scene:f.changes.scene,car:f.changes.car.length,singing:f.changes.singing.length,
            music:f.changes.music.length,performance:f.changes.performance.length,sources:f.sources.length},before);
        assert.equal(f.requests.filter(item=>item.url.startsWith('/api/singing/audio/')).length,1);
        assert.equal(f.frames.length,180,'the singing renderer still receives every visible animation frame');
    } finally {f.dispose();}
});

test('the adapter forwards the actual audio position through pause and resume without wall-clock choreography',async()=>{
    const f=await fixture();
    try {
        await f.start();
        const main=f.box.players[0],ctx=f.contexts[0];
        f.frame(40);assert.equal(f.frames.at(-1).timeline.playing,false,'preload lead is not an audible performance');
        ctx.currentTime=f.sources[0].when+9.25;f.frame(80);
        const first=f.frames.at(-1).timeline;
        assert.equal(first.moduleId,'singing');assert.equal(first.playing,true);
        assert.ok(Math.abs(first.position-9.25)<1e-9);assert.equal(first.bpm,84);assert.equal(first.motionStrength,.4);
        f.wall(9000);f.frame(120);
        assert.equal(f.frames.at(-1).timeline.position,first.position,'correcting the system clock cannot move the recording');
        main.pause();ctx.currentTime+=20;f.frame(160);
        const paused=f.frames.at(-1).timeline;
        assert.equal(paused.playing,false);assert.equal(paused.position,first.position);
        main.resume({...f.performance,starts_at:8990});await tick();
        ctx.currentTime=f.sources.at(-1).when+2;f.frame(200);
        assert.equal(f.frames.at(-1).timeline.playing,true);
        assert.ok(Math.abs(f.frames.at(-1).timeline.position-(first.position+2))<1e-9);
        assert.ok(Math.abs(f.sources.at(-1).offset-first.position)<1e-9);
        f.client.modules.setMuted(true);f.frame(240);
        assert.equal(f.frames.at(-1).timeline.playing,false,'muting stops performance motion rather than following elapsed wall time');
    } finally {f.dispose();}
});

test('a slow scene reveal never delays, restarts, or seeks the fixed recording',async()=>{
    const f=await fixture({transitions:true});
    try{
        const ready=f.holdScene('singing-room');await f.start();f.frame(40);
        const main=f.box.players[0],source=f.sources[0],when=source.when,offset=source.offset;
        assert.equal(f.scene.settings.background,'car','keep the old scene behind the transition frame');
        f.contexts[0].currentTime=when+8.5;f.frame(80);
        assert.ok(Math.abs(main.position-8.5)<1e-9);assert.equal(f.sources.length,1);
        ready();await tick();f.frame(120);
        assert.equal(f.scene.settings.background,'singing-room');assert.equal(f.sources.length,1);
        assert.equal(source.when,when);assert.equal(source.offset,offset);
        assert.ok(Math.abs(f.frames.at(-1).timeline.position-8.5)<1e-9);
    }finally{f.dispose();}
});
