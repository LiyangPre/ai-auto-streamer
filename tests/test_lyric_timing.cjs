const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');

async function fixture({moduleId='singing',preview=false,muted=false,cues,offset=0,delay=0}={}){
    const players=[],captions=[],requests=[],spoken=[];let wall=100,finished=0;
    const box={window:{},StudioModules:{script:async()=>{}},Date:{now:()=>wall*1000},
        setTimeout:()=>1,clearTimeout(){},fetch:(...args)=>{requests.push(args);return Promise.resolve({ok:true});}};
    box.SingingPlayer=class{
        constructor(options){this.options=options;this.loads=[];this.position=0;this._timelinePlaying=false;this._vocalPlaying=false;this.paused=false;this.muted=muted;players.push(this);}
        get timelinePlaying(){return !!this.performance&&this._timelinePlaying&&!this.paused&&!this.muted&&!preview;}
        get vocalPlaying(){return this.timelinePlaying&&this._vocalPlaying;}
        load(value){this.stop();this.performance=value;this.paused=false;this.loads.push(value);if(value)this.options.onStart(value);}
        stop(reason='stopped'){const p=this.performance;this.performance=null;this._timelinePlaying=false;this._vocalPlaying=false;if(p)this.options.onStop({reason,performance:p});}
        pause(){this.paused=true;}
        resume(value){this.performance=value;this.paused=false;this.options.onStart(value);}
        unlock(){}setMuted(value){this.muted=value;}dispose(){this.stop();}
    };
    vm.createContext(box);vm.runInContext(fs.readFileSync('frontend/static/modules/performance-output.js','utf8'),box);
    const client={isPreview:preview,muted,stopAudio(){},playAudio:(...args)=>spoken.push(args),
        modules:{presentation:()=>({pose:'idle',expression:'neutral',motion_strength:.3})},
        expressionController:{setModule(){},setExpression(){},setSpeaking(){}},carScene:{setSinging(){},setExpression(){}},
        renderer:{setAction(){},face:{setExpression(){}}},music:{setSpeaking(){}},
        subtitles:{begin(){},show:text=>captions.push(text),finish(){finished++;}},voiceEnvelope:{reset(){}}};
    const adapter=await box.window.createPerformanceAdapter(client,moduleId);
    const performance={id:'take-one',song:{id:'a'.repeat(32),title:'本地歌词测试',url:'/api/singing/audio/'+'a'.repeat(32),duration:60,
        lyrics_cues:cues??[{at:3,text:'第一句'},{at:10,text:'第二句'},{at:20,text:'第三句'}],lyrics_offset:offset},
        starts_at:103,vocal_delay:delay,vocal_volume:.85,music:'quiet',bpm:80,
        presentation:{pose:'idle',expression:moduleId==='singing'?'singing':'neutral',motion_strength:.3}};
    adapter.handle({type:moduleId+'_start',performance});
    const main=players[0];
    return {adapter,performance,main,players,captions,requests,spoken,client,
        setPlaying(value=true){main._timelinePlaying=value;main._vocalPlaying=value;},
        wall:value=>wall=value,last:()=>captions.at(-1),finished:()=>finished};
}

test('lyrics use the audible vocal clock and update once per line without moving the song',async()=>{
    const f=await fixture(),starts=f.performance.starts_at,duration=f.performance.song.duration;
    f.wall(9999);f.main.position=12;f.adapter.frame({});assert.match(f.last(),/本地歌词测试/);
    f.setPlaying();f.main.position=2.9;f.adapter.frame({});assert.match(f.last(),/本地歌词测试/);
    f.main.position=3.01;f.adapter.frame({});assert.equal(f.last(),'第一句');
    const count=f.captions.length;f.main.position=6;f.adapter.frame({});f.adapter.frame({});assert.equal(f.captions.length,count);
    f.main.position=10;f.adapter.frame({});assert.equal(f.last(),'第二句');
    assert.equal(f.performance.starts_at,starts);assert.equal(f.performance.song.duration,duration);
    assert.equal(f.requests.length,0);assert.equal(f.spoken.length,0);
});

test('positive lyric offset delays captions and vocal delay never advances a negative-delay recording',async()=>{
    for(const scenario of [{delay:2,offset:1.5,threshold:8.5},{delay:0,offset:-1,threshold:4},{delay:-2,offset:0,threshold:5}]){
        const f=await fixture({...scenario,cues:[{at:5,text:'进入这一句'}]});f.setPlaying();
        f.main.position=scenario.threshold-.01;f.adapter.frame({});assert.match(f.last(),/本地歌词测试/);
        f.main.position=scenario.threshold+.01;f.adapter.frame({});assert.equal(f.last(),'进入这一句');
    }
});

test('segment playback keeps original LRC timestamps and honors the vocal delay and lyric offset',async()=>{
    const f=await fixture({delay:2,offset:.5,cues:[{at:10,text:'Before range'},{at:60,text:'Range opening'},{at:70,text:'Next line'}]});
    f.performance.song.duration=180;f.performance.start_offset=60;f.performance.play_duration=20;f.setPlaying();
    f.main.position=2.5;f.adapter.frame({});assert.equal(f.last(),'Range opening');
    f.main.position=12.5;f.adapter.frame({});assert.equal(f.last(),'Next line');
    assert.equal(f.performance.song.duration,180);
});

test('late joins and seeks select the current lyric instead of replaying previous lines',async()=>{
    const f=await fixture({cues:[{at:5,text:'早一点'},{at:15,text:'中间这一句'},{at:25,text:'已经唱到这里'}]});
    f.adapter.handle({module_snapshots:{singing:f.performance}});f.setPlaying();f.main.position=28;f.wall(4000);f.adapter.frame({});
    assert.equal(f.last(),'已经唱到这里');assert.equal(f.captions.filter(text=>text==='早一点').length,0);
    f.main.position=8;f.adapter.frame({});assert.equal(f.last(),'早一点');
    f.main.pause();const count=f.captions.length;f.main.position=30;f.adapter.frame({});assert.equal(f.captions.length,count);
    f.main.resume({...f.performance,starts_at:105});f.main.position=18;f.adapter.frame({});assert.equal(f.last(),'中间这一句');
    assert.equal(f.main.performance.starts_at,105);assert.equal(f.main.performance.song.duration,60);
});

test('live lyric edits and removal keep the recording and its playback position intact',async()=>{
    const f=await fixture();f.setPlaying();f.main.position=12;f.adapter.frame({});assert.equal(f.last(),'第二句');
    const loads=f.main.loads.length,starts=f.main.performance.starts_at;
    f.adapter.handle({type:'singing_lyrics_updated',song_id:'b'.repeat(32),lyrics_cues:[{at:0,text:'其他歌曲'}],lyrics_offset:0});
    f.adapter.frame({});assert.equal(f.last(),'第二句');
    f.adapter.handle({type:'singing_lyrics_updated',song_id:f.performance.song.id,lyrics_cues:[{at:10,text:'修改后的这一句'}],lyrics_offset:2});
    f.adapter.frame({});assert.equal(f.last(),'修改后的这一句');
    assert.equal(f.main.position,12);assert.equal(f.main.loads.length,loads);assert.equal(f.main.performance.starts_at,starts);
    f.adapter.handle({type:'singing_lyrics_updated',song_id:f.performance.song.id,lyrics_cues:[],lyrics_offset:0});
    f.adapter.frame({});assert.match(f.last(),/本地歌词测试/);
    assert.equal(f.main.loads.length,loads);assert.equal(f.requests.length,0);
});

test('removing lyrics during the spoken intro leaves that voice caption until music begins',async()=>{
    const f=await fixture();
    const p={...f.performance,id:'take-with-intro',intro:{text:'接下来唱首歌给你听。',
        audio_url:'/audio/tts_100_abcd1234.mp3',duration:1.2,starts_at:100.15}};
    f.adapter.handle({type:'singing_start',performance:p});assert.equal(f.last(),p.intro.text);
    f.adapter.handle({type:'singing_lyrics_updated',song_id:p.song.id,lyrics_cues:[],lyrics_offset:0});
    assert.equal(f.last(),p.intro.text);
    f.wall(103.2);f.setPlaying();f.adapter.frame({});assert.match(f.last(),/本地歌词测试/);
});

test('backing-only karaoke follows its clock while buffering, mute and silent previews cannot advance lyrics',async()=>{
    const f=await fixture();f.performance.vocal_volume=0;f.main._timelinePlaying=true;f.main.position=12;f.adapter.frame({});
    assert.equal(f.main.vocalPlaying,false);assert.equal(f.last(),'第二句');
    f.main._timelinePlaying=false;f.main.position=22;f.adapter.frame({});assert.notEqual(f.last(),'第三句');
    f.main._timelinePlaying=true;f.adapter.setMuted(true);f.adapter.frame({});assert.notEqual(f.last(),'第三句');
    f.adapter.setMuted(false);f.adapter.frame({});assert.equal(f.last(),'第三句');
    const silent=await fixture({preview:true});silent.setPlaying();silent.main.position=22;silent.adapter.frame({});assert.match(silent.last(),/本地歌词测试/);
});

test('timed blank lines clear captions and malformed cues cannot execute markup or break frames',async()=>{
    const literal='<img src=x onerror=alert(1)>';
    const f=await fixture({cues:[null,{},'bad',{at:Infinity,text:'bad'},{at:NaN,text:'bad'},
        {at:-1,text:'bad'},{at:'1',text:'bad'},{at:7,text:'x'.repeat(301)},
        {at:10,text:'Second language'},{at:5,text:literal},{at:10,text:'第二种语言'},{at:20,text:''}]});
    f.setPlaying();f.main.position=5;assert.doesNotThrow(()=>f.adapter.frame({}));assert.equal(f.last(),literal);
    f.main.position=8;f.adapter.frame({});assert.equal(f.last(),literal);
    f.main.position=10;f.adapter.frame({});assert.match(f.last(),/Second language/);assert.match(f.last(),/第二种语言/);
    f.main.position=20;f.adapter.frame({});assert.equal(f.last(),'');
    f.adapter.handle({type:'singing_lyrics_updated',song_id:f.performance.song.id,lyrics_cues:{at:2,text:'bad'},lyrics_offset:Infinity});
    assert.doesNotThrow(()=>f.adapter.frame({}));
});

test('stopping, a new recording and story mode cannot leak a previous song lyric',async()=>{
    const f=await fixture();f.setPlaying();f.main.position=12;f.adapter.frame({});assert.equal(f.last(),'第二句');
    f.adapter.handle({type:'singing_stop',playback_id:f.performance.id});const count=f.captions.length;
    f.adapter.frame({});assert.equal(f.captions.length,count);assert.ok(f.finished()>0);
    const next={...f.performance,id:'take-two',song:{id:'b'.repeat(32),title:'另一个录音',duration:60,url:'/api/singing/audio/'+'b'.repeat(32)}};
    f.adapter.handle({type:'singing_start',performance:next});f.setPlaying();f.main.position=25;f.adapter.frame({});assert.match(f.last(),/另一个录音/);
    assert.notEqual(f.last(),'第三句');
    const story=await fixture({moduleId:'story'});story.setPlaying();story.main.position=25;story.adapter.frame({});assert.match(story.last(),/本地歌词测试/);assert.notEqual(story.last(),'第三句');
});
