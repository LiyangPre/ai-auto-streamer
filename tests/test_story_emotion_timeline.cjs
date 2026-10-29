const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

async function fixture({expression='auto',enabled=true,moduleId='story'}={}){
    const players=[],expressions=[],carFaces=[],renderFaces=[],requests=[];let wall=100;
    const box={window:{},StudioModules:{script:async()=>{}},Date:{now:()=>wall*1000},setTimeout:()=>1,clearTimeout(){},
        fetch:(...args)=>{requests.push(args);return Promise.resolve({ok:true});}};
    box.SingingPlayer=class{
        constructor(options){this.options=options;this.position=0;this._timelinePlaying=false;this.paused=false;this.muted=false;players.push(this);}
        get timelinePlaying(){return !!this.performance&&this._timelinePlaying&&!this.paused&&!this.muted;}
        get vocalPlaying(){return this.timelinePlaying;}
        load(value){this.stop();this.performance=value;this.paused=false;if(value)this.options.onStart(value);}
        stop(reason='stopped'){const old=this.performance;this.performance=null;this._timelinePlaying=false;if(old)this.options.onStop({reason,performance:old});}
        pause(){this.paused=true;}
        resume(value){this.performance=value;this.paused=false;this.options.onStart(value);}
        unlock(){}setMuted(value){this.muted=value;}dispose(){this.stop();}
    };
    vm.createContext(box);vm.runInContext(fs.readFileSync('frontend/static/modules/performance-output.js','utf8'),box);
    const client={isPreview:false,muted:false,stopAudio(){},playAudio(){},
        modules:{presentation:()=>({expression:'neutral',pose:'idle',motion_strength:.3})},
        expressionController:{setModule(){},setExpression:(emotion,options)=>expressions.push({emotion,...options}),setSpeaking(){}},
        carScene:{setSinging(){},setExpression:emotion=>carFaces.push(emotion)},
        renderer:{setAction(){},face:{setExpression:(...args)=>renderFaces.push(args)}},
        music:{setSpeaking(){}},subtitles:{begin(){},show(){},finish(){}},voiceEnvelope:{reset(){}}};
    const adapter=await box.window.createPerformanceAdapter(client,moduleId);
    const performance={id:'story-one',song:{title:'三人合租小故事',url:'/api/stories/audio/astra/'+'a'.repeat(32),duration:40,
        expression_cues:[{at:0,emotion:'neutral'},{at:5,emotion:'happy'},{at:12,emotion:'thinking'},{at:23,emotion:'shy'}]},
        starts_at:103,vocal_volume:.85,vocal_delay:0,music:'shared',bpm:60,
        presentation:{pose:'idle',expression,expression_enabled:enabled,motion_strength:.25}};
    adapter.handle({type:moduleId+'_start',performance});
    return {adapter,performance,players,expressions,carFaces,renderFaces,requests,
        main:players[0],advanceWall:value=>wall=value,cues:()=>expressions.filter(item=>item.source==='story_cue')};
}

test('story faces follow audio position and seeking while leaving the recording clock intact',async()=>{
    const f=await fixture(),starts=f.performance.starts_at,duration=f.performance.song.duration;
    f.main._timelinePlaying=true;f.advanceWall(9999);f.main.position=0;f.adapter.frame({});
    assert.equal(f.cues().at(-1).emotion,'neutral');assert.equal(f.carFaces.at(-1),'normal');
    const count=f.cues().length;f.main.position=4.9;f.adapter.frame({});assert.equal(f.cues().length,count);
    f.main.position=5.1;f.adapter.frame({});assert.equal(f.cues().at(-1).emotion,'happy');
    f.main.position=18;f.adapter.frame({});assert.equal(f.cues().at(-1).emotion,'thinking');
    // A seek backwards follows the vocal timeline rather than elapsed wall time.
    f.main.position=2;f.adapter.frame({});assert.equal(f.cues().at(-1).emotion,'neutral');
    assert.equal(f.performance.starts_at,starts);assert.equal(f.performance.song.duration,duration);
    assert.equal(f.requests.length,0);
});

test('explicit neutral, disabled expressions and singing keep their selected module look',async()=>{
    for(const options of [{expression:'neutral'},{enabled:false},{moduleId:'singing'}]){
        const f=await fixture(options);f.main._timelinePlaying=true;f.main.position=25;f.adapter.frame({});
        assert.equal(f.cues().length,0);
    }
});

test('no story cue is applied before audible audio or while muted',async()=>{
    const f=await fixture();f.main.position=18;f.advanceWall(400);f.adapter.frame({});assert.equal(f.cues().length,0);
    f.main._timelinePlaying=true;f.adapter.setMuted(true);f.adapter.frame({});assert.equal(f.cues().length,0);
    f.adapter.setMuted(false);f.adapter.frame({});assert.equal(f.cues().at(-1).emotion,'thinking');
});

test('welcome interludes suspend story emotions and resume at the same audio cue',async()=>{
    const f=await fixture();f.main._timelinePlaying=true;f.main.position=18;f.adapter.frame({});
    assert.equal(f.cues().at(-1).emotion,'thinking');const count=f.cues().length;
    f.adapter.handle({type:'performance_welcome',module_id:'story',playback_id:f.performance.id,
        greeting:{id:'welcome-one',text:'欢迎回来。',duration:1,audio_url:'/audio/tts_100_abcd1234.mp3',starts_at:120}});
    assert.equal(f.main.paused,true);assert.equal(f.expressions.at(-1).emotion,'happy');
    f.advanceWall(140);f.adapter.frame({});assert.equal(f.cues().length,count);
    f.adapter.handle({type:'performance_resume',module_id:'story',performance:{...f.performance,starts_at:130}});
    f.adapter.frame({});assert.equal(f.cues().length,count+1);assert.equal(f.cues().at(-1).emotion,'thinking');
    f.main.position=25;f.adapter.frame({});assert.equal(f.cues().at(-1).emotion,'shy');
    assert.equal(f.main.performance.starts_at,130);assert.equal(f.main.performance.song.duration,40);
});

test('unknown, nonfinite and malformed emotion metadata cannot break the frame loop',async()=>{
    const f=await fixture();f.main._timelinePlaying=true;f.main.position=10;
    f.performance.song.expression_cues=[null,{},'bad',{at:NaN,emotion:'happy'},{at:Infinity,emotion:'happy'},
        {at:-1,emotion:'sad'},{at:'2',emotion:'happy'},{at:3,emotion:'unsupported-face'},{at:2,emotion:'happy'}];
    assert.doesNotThrow(()=>f.adapter.frame({}));assert.equal(f.cues().at(-1).emotion,'happy');
    const count=f.cues().length;f.performance.song.expression_cues={at:3,emotion:'sad'};
    assert.doesNotThrow(()=>f.adapter.frame({}));assert.equal(f.cues().length,count);
});

test('stop clears story state and a new performance applies its own cues',async()=>{
    const f=await fixture();f.main._timelinePlaying=true;f.main.position=18;f.adapter.frame({});
    f.adapter.handle({type:'story_stop',playback_id:f.performance.id});const count=f.cues().length;
    f.adapter.frame({});assert.equal(f.cues().length,count);assert.equal(f.expressions.at(-1).source,'module_return');
    const next={...f.performance,id:'story-two'};f.adapter.handle({type:'story_start',performance:next});
    f.main._timelinePlaying=true;f.main.position=25;f.adapter.frame({});assert.equal(f.cues().length,count+1);
    assert.equal(f.cues().at(-1).emotion,'shy');
});
