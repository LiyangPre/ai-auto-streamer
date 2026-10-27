const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture({preview=false,blocked=false,defer=false}={}){
    const sources=[],gains=[],music=[],timers=new Map();let now=100000,resolveFetch,stops=0,contexts=0,starts=0;
    class Context {
        constructor(){contexts++;this.currentTime=10;this.state=blocked?'suspended':'running';this.destination={kind:'output'};}
        createAnalyser(){return {kind:'vocal-analyser',frequencyBinCount:512,connect(){}};}
        async resume(){}
        async decodeAudioData(){return {duration:60};}
        createBufferSource(){const source={connect(gain){this.gain=gain;},start(when,offset){this.when=when;this.offset=offset;},stop(){this.stopped=true;},disconnect(){}};sources.push(source);return source;}
        createGain(){const gain={gain:{value:0},connect(target){this.target=target;},disconnect(){}};gains.push(gain);return gain;}
        close(){}
    }
    const response=()=>({ok:true,headers:{get:()=>100},arrayBuffer:async()=>new ArrayBuffer(4)});
    const box={window:{AudioContext:Context},AbortController,Float32Array,Uint8Array,Date:{now:()=>now},
        setTimeout:callback=>{const id=timers.size+1;timers.set(id,callback);return id;},clearTimeout:id=>timers.delete(id),
        fetch:()=>defer?new Promise(resolve=>resolveFetch=()=>resolve(response())):Promise.resolve(response())};
    vm.createContext(box);vm.runInContext(fs.readFileSync('frontend/static/singing-player.js','utf8'),box);
    const player=new box.window.SingingPlayer({preview,music:{setPerformance:mode=>music.push(mode)},onStop:()=>stops++,onStart:()=>starts++});
    const performance={id:'take1',song:{url:'/api/singing/audio/'+'a'.repeat(32),duration:60,title:'Demo'},
        starts_at:100,vocal_delay:2,backing_url:'/media/'+'b'.repeat(32)+'.mp3',vocal_volume:.8,backing_volume:.2,music:'accompaniment',bpm:80};
    return {player,performance,sources,gains,music,timers,advance:seconds=>now+=seconds*1000,resolve:()=>resolveFetch?.(),contexts:()=>contexts,stops:()=>stops,starts:()=>starts};
}
test('vocals and instrumental use one clock, with accompaniment excluded from lip sync',async()=>{
    const f=fixture();await f.player.load(f.performance);
    assert.equal(f.contexts(),1);assert.equal(f.sources.length,2);
    assert.ok(Math.abs(f.sources[0].when-f.sources[1].when-2)<.0001);
    assert.equal(f.gains[0].target.kind,'vocal-analyser');assert.equal(f.gains[1].target.kind,'output');
    assert.equal(f.player.vocalPlaying,false);f.player.ctx.currentTime=13;assert.equal(f.player.vocalPlaying,true);
    f.player.stop();assert.ok(f.sources.every(s=>s.stopped));assert.equal(f.music.at(-1),null);assert.equal(f.stops(),1);
});
test('late output joins at elapsed position, while thumbnails stay silent',async()=>{
    const f=fixture();f.advance(12);await f.player.load(f.performance);
    assert.equal(f.sources[0].offset,10);assert.equal(f.sources[1].offset,12);f.player.stop();
    const preview=fixture({preview:true});await preview.player.load(preview.performance);
    assert.equal(preview.contexts(),0);assert.equal(preview.player.vocalPlaying,false);assert.equal(preview.music.length,1);preview.player.stop();
});
test('blocked autoplay can resume once, duplicate start cannot create doubled voices',async()=>{
    const f=fixture({blocked:true});await f.player.load(f.performance);assert.equal(f.sources.length,0);
    f.player.ctx.state='running';await Promise.all([f.player.unlock(),f.player.unlock()]);await f.player.load(f.performance);await f.player.unlock();
    assert.equal(f.sources.length,2);f.player.stop();
});
test('stop while fetching prevents delayed audio from starting',async()=>{
    const f=fixture({defer:true});f.performance.backing_url=null;
    const loading=f.player.load(f.performance);f.player.stop();f.resolve();await loading;
    assert.equal(f.sources.length,0);assert.equal(f.player.performance,null);
});

test('welcome pause freezes the exact audio position and resumes without restarting the story',async()=>{
    const f=fixture();f.performance.song.url='/api/stories/audio/astra/'+'a'.repeat(32);f.performance.backing_url=null;f.performance.vocal_delay=0;
    await f.player.load(f.performance);f.player.ctx.currentTime=15;
    const position=f.player.position;f.player.pause();f.advance(20);f.player.ctx.currentTime=35;
    assert.equal(f.player.position,position);assert.equal(f.player.vocalPlaying,false);assert.equal(f.stops(),0);
    f.player.resume({...f.performance,starts_at:120});await f.player.unlock();
    assert.ok(Math.abs(f.sources.at(-1).offset-position)<.001);assert.equal(f.stops(),0);
    f.player.stop();assert.equal(f.stops(),1);
});

test('optional vocal guide drives silent lip analysis while the full recording plays once',async()=>{
    const f=fixture();f.performance.backing_url=null;f.performance.song.guide_url=f.performance.song.url+'/guide';
    await f.player.load(f.performance);
    assert.equal(f.sources.length,2);assert.equal(f.sources[0].gain.target.kind,'output');
    assert.equal(f.sources[1].gain.target,f.player.guideAnalyser);assert.equal(f.player.guideMute.gain.value,0);
    assert.equal(f.player.lipAnalyser,f.player.guideAnalyser);f.player.stop();
});

test('paused reconnect survives the original end time and resumes the saved position',async()=>{
    const f=fixture();f.advance(80);f.performance.paused=true;f.performance.paused_at=110;f.performance.backing_url=null;f.performance.vocal_delay=0;
    await f.player.load(f.performance);assert.equal(f.player.performance.id,'take1');assert.equal(f.sources.length,0);
    assert.equal(f.player.position,10);f.player.resume({...f.performance,paused:false,starts_at:170});await f.player.unlock();
    assert.equal(f.sources[0].offset,10);f.player.stop();
});

test('silent previews restore the story visual after a welcome, without creating audio',async()=>{
    const f=fixture({preview:true});await f.player.load(f.performance);f.player.pause();f.player.resume(f.performance);
    assert.equal(f.starts(),2);assert.equal(f.contexts(),0);f.player.stop();
});

test('returning from page disposal recreates guide nodes in the new audio context',async()=>{
    const f=fixture();f.performance.song.guide_url=f.performance.song.url+'/guide';await f.player.load(f.performance);
    const oldGuide=f.player.guideAnalyser;f.player.dispose();await f.player.load({...f.performance,id:'second'});
    assert.equal(f.contexts(),2);assert.notEqual(f.player.guideAnalyser,oldGuide);assert.equal(f.player.lipAnalyser,f.player.guideAnalyser);f.player.stop();
});

test('song preparation progress is read-only and renders literal titles with bounded counts',()=>{
    const box={StudioModules:{registerController(){}},console};vm.createContext(box);
    vm.runInContext(fs.readFileSync('frontend/static/singing-controls.js','utf8'),box);
    const Controls=vm.runInContext('SingingControls',box),controller=Object.create(Controls.prototype),classes=[];
    controller.preparationPanel={classList:{toggle:(...value)=>classes.push(value)}};
    controller.preparationText={textContent:''};controller.preparationProgress={};
    controller.showPreparation({ready:3,total:9,stage:'converting',state:'running',current_title:'<img src=x>',estimated_seconds_remaining:121});
    assert.equal(controller.preparationProgress.value,3);assert.equal(controller.preparationProgress.max,9);
    assert.match(controller.preparationText.textContent,/3 \/ 9/);assert.match(controller.preparationText.textContent,/<img src=x>/);
    assert.match(controller.preparationText.textContent,/3 分钟/);assert.equal(classes.at(-1)[1],false);
    controller.showPreparation({ready:100,total:100,state:'error'});
    assert.equal(controller.preparationProgress.value,64);assert.equal(controller.preparationProgress.max,64);assert.equal(classes.at(-1)[1],true);
    controller.showPreparation({ready:3,total:4,stage:'converting'});
    assert.equal(controller.preparationProgress.max,4);assert.equal(controller.preparationProgress.value,3);
});
