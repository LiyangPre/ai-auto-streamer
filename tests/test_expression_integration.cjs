const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

function runtime(){
    const requests=[],audios=[];
    const box={window:{addEventListener(){},removeEventListener(){},devicePixelRatio:1},
        console,performance,URLSearchParams,location:{search:'',origin:'http://localhost'},
        setTimeout,clearTimeout,Map,AbortSignal,
        fetch:(...args)=>{requests.push(args);return Promise.reject(Error('Unexpected expression request'));},
        Audio:class{constructor(...args){audios.push(args);throw Error('Unexpected expression audio');}},
        getComputedStyle:()=>({width:'720px',height:'540px'})};
    vm.createContext(box);
    for(const file of ['character-motion.js','character-face.js','vendor/expression-engine.bundle.js','app.js'])
        vm.runInContext(fs.readFileSync('frontend/static/'+file,'utf8'),box);
    vm.runInContext('this.Client=AIStreamerClient;',box);
    return {box,requests,audios,Controller:box.window.CharacterExpressionController};
}

function settle(controller,count=120){
    let state;
    for(let i=0;i<count;i++)state=controller.sample(1/60,{mouthOpen:0,mouthRound:0});
    return state;
}

function clientFixture(){
    const f=runtime(),client=Object.create(f.box.Client.prototype);
    const look={pose:'auto',expression:'auto',motion_strength:.3,expression_enabled:true,expression_strength:.55};
    const element=()=>({textContent:'',classList:{add(){},remove(){}}});
    Object.assign(client,{expressionController:new f.Controller({profileId:'astra',seed:13}),
        activeProfileId:'astra',modules:{presentation:()=>look},muted:false,isPreview:true,
        voiceEnabled:true,emotionIcons:{normal:'neutral',happy:'happy',thinking:'thinking',surprised:'surprised'},
        elements:{emotionIcon:element(),emotionText:element(),responseText:element()},
        subtitles:{waitForSpeech(){},begin(){},finish(){},show(){}},
        animator:{setTarget(){}},renderer:{setAction(){},setSpeaking(){},face:{setExpression(){}}},
        carScene:{setExpression(){},setAction(){}},applyScene(){},stopAudio(){},addDanmakuToFeed(){}});
    return {...f,client,look};
}

test('existing GLM emotion tags drive local expressions without another request or playback',()=>{
    const {client,requests,audios}=clientFixture();
    client.handleResponseStart({emotion:'happy',action:{name:'idle',emotion:'happy'}});
    const happy=settle(client.expressionController);
    assert.equal(happy.engine,'soullink','the prototype must exercise the pinned SDK');
    assert.ok(happy.smile>.3);
    client.updateEmotion('thinking');
    const thinking=settle(client.expressionController);
    assert.ok(thinking.smile<happy.smile);
    client.updateEmotion('unknown-emotion');
    const normal=settle(client.expressionController);
    assert.equal(normal.eyeOpenL,normal.eyeOpenR);
    assert.ok(normal.smile<happy.smile);
    assert.equal(client.elements.emotionText.textContent,'normal');
    assert.equal(requests.length,0);assert.equal(audios.length,0);
});

test('module stop and welcome resume restore the module look, then chat, without stale held emotions',async()=>{
    const {box,Controller,requests,audios}=runtime(),players=[];
    box.StudioModules={script:async()=>{}};
    box.SingingPlayer=class{
        constructor(options){this.options=options;this.position=1;this.vocalPlaying=false;players.push(this);}
        load(value){this.stop();this.performance=value;if(value)this.options.onStart(value);}
        stop(reason='stopped'){const p=this.performance;this.performance=null;if(p)this.options.onStop({reason,performance:p});}
        pause(){this.paused=true;this.vocalPlaying=false;}
        resume(value){this.performance=value;this.paused=false;}
        unlock(){}setMuted(value){this.muted=value;}dispose(){this.stop();}
    };
    vm.runInContext(fs.readFileSync('frontend/static/modules/performance-output.js','utf8'),box);
    const chat={pose:'auto',expression:'neutral',motion_strength:.3,expression_enabled:true,expression_strength:.55};
    const controller=new Controller({profileId:'astra',seed:21});
    const calls=[],originalSet=controller.setExpression.bind(controller);
    controller.setExpression=(name,options)=>{calls.push({name,options});return originalSet(name,options);};
    const client={isPreview:true,muted:true,expressionController:controller,stopAudio(){},
        modules:{presentation:()=>chat},carScene:{setSinging(){},setExpression(){}},
        renderer:{setAction(){},face:{setExpression(){}}},music:{setSpeaking(){}},
        subtitles:{begin(){},show(){},finish(){}},voiceEnvelope:{reset(){}}};
    const adapter=await box.window.createPerformanceAdapter(client,'story');
    const presentation={...chat,expression:'thinking'},performance={id:'story-id',song:{title:'Story'},bpm:80,presentation};
    adapter.handle({type:'story_start',performance});
    assert.equal(adapter.moduleId,'story');assert.equal(adapter.active,true);
    const story=settle(controller);
    adapter.handle({type:'performance_welcome',module_id:'story',playback_id:performance.id,
        greeting:{id:'hello',text:'欢迎',duration:2,audio_url:'/audio/hello.mp3'}});
    const welcome=settle(controller);
    assert.ok(welcome.smile>story.smile);
    assert.equal(calls.at(-1).options.hold,false,'a greeting must not pin a happy face indefinitely');
    adapter.handle({type:'performance_resume',module_id:'story',performance});
    assert.equal(calls.at(-1).name,'thinking');
    assert.ok(settle(controller).smile<welcome.smile);
    adapter.handle({type:'story_stop'});
    assert.equal(adapter.active,false);assert.equal(calls.at(-1).name,'neutral');
    const restored=settle(controller);
    assert.equal(restored.eyeOpenL,restored.eyeOpenR);assert.equal(restored.mouthOpen,0);
    assert.equal(requests.length,0);assert.equal(audios.length,0);
    adapter.dispose();
});

test('mute, pauses and silence close the mouth even in surprised or singing expressions',()=>{
    const {Controller}=runtime();
    const controller=new Controller({profileId:'astra',seed:9});
    for(const expression of ['happy','surprised','singing']){
        controller.setExpression(expression,{hold:true});controller.setSpeaking(true);
        assert.equal(controller.sample(1/30,{mouthOpen:.7,mouthRound:.2}).mouthOpen,.7);
        assert.equal(controller.sample(1/30,{mouthOpen:0,mouthRound:0}).mouthOpen,0,'expression cannot invent lip movement in silence');
        controller.setSpeaking(false);
        assert.equal(controller.sample(1/30,{mouthOpen:.7,mouthRound:.2}).mouthOpen,0,'paused or muted audio must close the mouth');
    }
});

test('an expression change during a reaction blends from its current pose',()=>{
    const {Controller}=runtime(),controller=new Controller({profileId:'astra',seed:4});
    controller.setExpression('happy',{hold:true});
    const happy=settle(controller);
    controller.setExpression('sad',{hold:true});
    const next=controller.sample(1/60,{mouthOpen:0,mouthRound:0});
    assert.ok(Math.abs(next.smile-happy.smile)<.12,'interrupting a reaction must not snap to the next target');
    const sad=settle(controller);
    assert.ok(sad.smile<happy.smile-.04,'the blended face must move toward its new expression');
    assert.equal(next.eyeOpenL,next.eyeOpenR);
    assert.equal(next.mouthOpen,0);
});

test('switching profiles discards held emotion and speech while retaining reusable scene settings',()=>{
    const {client,requests,audios}=clientFixture();
    client.expressionController.setExpression('happy',{hold:true});
    const previous=settle(client.expressionController);
    client.expressionController.setSpeaking(true);
    assert.equal(client.expressionController.sample(1/30,{mouthOpen:.8}).mouthOpen,.8);
    const sharedScene={background:'car',music_asset:'a'.repeat(32)};
    let scene;
    client.applyScene=value=>{scene=value;};
    client.applyStudioSettings({profile_changed:true,profile_context:{active_id:'mira'},
        settings:{scene:sharedScene,identity:{name:'Mira'},voice:{enabled:true},actions:{intensity:.4}}});
    const current=settle(client.expressionController);
    assert.equal(client.activeProfileId,'mira');
    assert.equal(client.expressionController.profileId,'mira');
    assert.ok(current.smile<previous.smile-.1,'the previous character must not leave a held reaction behind');
    assert.equal(current.mouthOpen,0);assert.equal(scene,sharedScene);
    assert.equal(requests.length,0);assert.equal(audios.length,0);
});

test('queued speech selects its own emotion on playback and ignores late callbacks from an old cue',async()=>{
    const {box,Controller,requests}=runtime(),created=[];
    box.Audio=class{
        constructor(url){this.url=url;this.paused=true;this.volume=1;this.muted=false;created.push(this);}
        async play(){this.paused=false;}
        pause(){this.paused=true;}
    };
    box.window.AudioContext=class{
        state='running';destination={};
        createAnalyser(){return {frequencyBinCount:128,connect(){}};}
        createMediaElementSource(){return {connect(){},disconnect(){}};}
        async resume(){}
    };
    const client=Object.create(box.Client.prototype);
    Object.assign(client,{expressionController:new Controller({profileId:'astra',seed:6}),
        audioQueue:[],audioPlaying:false,audioGeneration:0,audioContext:null,analyser:null,
        renderer:{setSpeaking(){}},animator:{setTarget(){}},muted:false,isPreview:false,isSpeaking:false,
        subtitles:{setAudioBusy(){},show(){},finish(){}}});
    const first=client.playAudio('/first.mp3','First line','happy');
    const second=client.playAudio('/second.mp3','Second line','sad');
    const tick=()=>new Promise(resolve=>setImmediate(resolve));
    await tick();
    assert.equal(client.expressionController.expression,'neutral','loading an audio URL is not speech playback');
    created[0].onplaying();assert.equal(client.expressionController.expression,'happy');
    created[0].onended();await tick();
    created[1].onplaying();assert.equal(client.expressionController.expression,'sad');
    created[0].onplaying();created[0].onpause();
    assert.equal(client.expressionController.expression,'sad','old media callbacks cannot replace the next cue emotion');
    assert.equal(client.isSpeaking,true,'an old pause event cannot close the active voice');
    created[1].onended();await Promise.all([first,second]);
    assert.equal(client.isSpeaking,false);
    assert.equal(client.expressionController.sample(1/30,{mouthOpen:.8}).mouthOpen,0);
    assert.equal(created.length,2);assert.equal(requests.length,0);
});

test('all painted-car expressions use paired eye openness and a closed silent mouth',()=>{
    const {box}=runtime(),scales=[],fills=[];
    const ctx={clearRect(){},drawImage(){},setTransform(){},save(){},restore(){},translate(){},rotate(){},
        scale:(x,y)=>scales.push([x,y]),beginPath(){},moveTo(){},quadraticCurveTo(){},bezierCurveTo(){},lineTo(){},ellipse(){},
        stroke(){},fill:()=>fills.push(1),clip(){},closePath(){},fillRect(){},createRadialGradient:()=>({addColorStop(){}})};
    const canvas={width:720,height:540,getContext:()=>ctx};
    const layer={dataset:{},style:{setProperty(){}},querySelector:s=>s==='.car-face'?canvas:s==='img'?{}:{}};
    vm.runInContext(fs.readFileSync('frontend/static/car-scene.js','utf8')+'\nthis.Car=CarScene;',box);
    const scene=new box.Car(layer);scene.blinkClock.sample=()=>({blink:0});
    const eyes=[{bounds:[395,270,115,71],canvas:{}},{bounds:[557,248,108,73],canvas:{}}];
    for(const name of Object.keys(box.Car.expressions)){
        scene.setExpression(name);for(let i=0;i<60;i++)scene.sample(1/30);
        const state=scene.sample(1/30);scales.length=0;
        scene.drawEye(eyes[0],0,state);scene.drawEye(eyes[1],1,state);
        assert.equal(scales[0][1],scales[1][1],name+' must not stretch one eye differently');
        fills.length=0;scene.drawMouth(0,0,state);
        assert.equal(fills.length,0,name+' must not open a silent mouth');
    }
    scene.dispose();
});
