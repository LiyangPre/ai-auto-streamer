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

function requestControls(){
    class Element{
        constructor(){this.children=[];this.textContent='';this.value='';this.checked=false;this.classList={toggle(){}};}
        append(...children){this.children.push(...children);}
        appendChild(child){this.children.push(child);}
        replaceChildren(...children){this.children=[...children];}
        setAttribute(){}
        set innerHTML(value){throw Error('Viewer text must never be interpreted as HTML');}
    }
    const nodes=new Map(),box={StudioModules:{registerController(){}},document:{createElement:()=>new Element()},console,TextDecoder,TextEncoder};
    vm.createContext(box);vm.runInContext(fs.readFileSync('frontend/static/singing-controls.js','utf8'),box);
    const Controls=vm.runInContext('SingingControls',box),controller=Object.create(Controls.prototype);
    controller.$=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
    const data={profile_id:'astra',revision:7,requests:{policy:{chat_requests:true,auto_play:true,max_queue:10,user_cooldown:90,song_cooldown:300,intermission_seconds:15},queue:[],last_result:null}};
    return {controller,data,nodes};
}

test('song-request polling preserves edited policy and its original revision for conflict detection',async()=>{
    const {controller:c,data}=requestControls();c.showRequests(data);
    c.$('sing-request-intermission').value='25';c.requestPolicyDirty=true;
    c.showRequests({...data,revision:8,requests:{...data.requests,policy:{...data.requests.policy,intermission_seconds:30}}});
    assert.equal(c.$('sing-request-intermission').value,'25');assert.equal(c.requestPolicyRevision,7);
    c.data={...data,revision:8};let sent,refreshes=0;
    c.request=async(path,body)=>{sent={path,body};throw Error('版本已更新，请重新加载');};c.refresh=async()=>refreshes++;
    await c.saveRequestPolicy();
    assert.equal(sent.path,'/request-policy');assert.equal(sent.body.revision,7);
    assert.equal(sent.body.policy.intermission_seconds,25);assert.equal(sent.body.policy.max_queue,10);
    assert.equal(c.$('sing-request-intermission').value,'25');assert.equal(c.requestPolicyDirty,true);assert.equal(refreshes,0);
    c.showRequests({...data,profile_id:'other',revision:8});
    assert.equal(c.requestPolicyDirty,false);assert.equal(Number(c.$('sing-request-intermission').value),15);assert.equal(c.requestPolicyProfile,'other');
});

test('song queue renders literal viewer names and titles and removes the exact request',async()=>{
    const {controller:c,data}=requestControls(),title='<img src=x onerror=alert(1)>',name='<script>bad()</script>';
    data.requests.queue=[{id:'queue-one',title,requester:name}];data.requests.last_result={accepted:false,message:'<b>still literal</b>'};c.showRequests(data);
    const row=c.$('sing-request-list').children[0];assert.equal(row.children[0].children[0].textContent,'1. '+title);
    assert.equal(row.children[0].children[1].textContent,'Requested by · '+name);
    assert.equal(c.$('sing-request-result').textContent,'<b>still literal</b>');
    let action;c.requestAction=async(path,body)=>action={path,body};await row.children[1].onclick();
    assert.equal(action.path,'/requests/remove');assert.equal(action.body.request_id,'queue-one');
});

test('adding a song to requests uses only the queue endpoint and leaves playback untouched',async()=>{
    const {controller:c}=requestControls();c.$('sing-songs').value='saved-song';let route,body,selections=0;
    c.requestAction=async(path,value)=>{route=path;body=value;};c.select=()=>selections++;
    await c.queueSelected();assert.equal(route,'/requests');assert.equal(body.song_id,'saved-song');assert.equal(selections,1);
    c.showRequests({profile_id:'astra',revision:1});assert.equal(c.$('sing-request-card').hidden,true);
});

test('blocked-song drafts survive polling and save the complete reversible policy',async()=>{
    const {controller:c,data}=requestControls();
    data.requests.policy.blocked_titles=['告白气球'];c.data=data;c.showRequests(data);
    assert.equal(c.$('sing-blocked-titles').value,'告白气球');
    c.$('sing-blocked-titles').value=' 海屿你 \n告白气球\n海屿你';c.requestPolicyDirty=true;
    c.showRequests({...data,revision:8,requests:{...data.requests,policy:{...data.requests.policy,blocked_titles:[]}}});
    assert.equal(c.$('sing-blocked-titles').value,' 海屿你 \n告白气球\n海屿你');assert.equal(c.requestPolicyRevision,7);
    let sent;c.request=async(path,body)=>{sent={path,body};throw Error('版本冲突');};c.refresh=async()=>{};
    await c.saveRequestPolicy();
    assert.equal(sent.path,'/request-policy');assert.equal(sent.body.revision,7);
    assert.deepEqual(Array.from(sent.body.policy.blocked_titles),['海屿你','告白气球']);
    assert.equal(sent.body.policy.max_queue,10);assert.equal(c.requestPolicyDirty,true);
    c.requestPolicyDirty=false;c.requestPolicyRevision=null;
    c.showRequests({...data,revision:9,requests:{...data.requests,policy:{...data.requests.policy,blocked_titles:[]}}});
    assert.equal(c.$('sing-blocked-titles').value,'');
});

test('banned songs disable streaming and queueing while preserving local audition',async()=>{
    const {controller:c,data}=requestControls(),song={id:'banned-song',title:'告白气球',blocked:true,duration:12,bytes:1000,source:'upload',url:'/api/singing/audio/saved'};
    c.data={...data,songs:[song]};c.$('sing-songs').value=song.id;c.$('sing-audition').pause=()=>{};
    c.select();assert.equal(c.$('sing-play').disabled,true);assert.equal(c.$('sing-request-add').disabled,true);
    assert.equal(c.$('sing-audition').src,song.url);assert.match(c.$('sing-song-info').textContent,/已暂禁舞台播放/);
    let requests=0;c.request=async()=>requests++;c.requestAction=async()=>requests++;
    await c.queueSelected();await c.play();assert.equal(requests,0);
    song.blocked=false;c.select();assert.equal(c.$('sing-play').disabled,false);assert.equal(c.$('sing-request-add').disabled,false);
});

function lrcFile(bytes,name='song.lrc'){
    bytes=Buffer.from(bytes);return {name,size:bytes.length,arrayBuffer:async()=>Uint8Array.from(bytes).buffer};
}

test('per-song duration defaults to full, parses clock times and preserves edits across polling',async()=>{
    const {controller:c,data}=requestControls(),song={id:'one',duration:200};
    c.data={...data,songs:[song]};c.$('sing-songs').value=song.id;c.showPlaybackRange(song);
    assert.equal(c.$('sing-range-full').checked,true);assert.equal(c.$('sing-range-start').disabled,true);
    assert.equal(c.parseRangeTime('1:30.5'),90.5);assert.equal(c.parseRangeTime('90.5'),90.5);
    for(const value of ['1:90','-1','1e3','Infinity','hello',''])assert.throws(()=>c.parseRangeTime(value));
    c.$('sing-range-full').checked=false;c.$('sing-range-start').value='1:00';c.$('sing-range-end').value='1:30';c.rangeDirty=true;
    c.showPlaybackRange({...song,playback_range:{start_seconds:2,end_seconds:4}});
    assert.equal(c.$('sing-range-start').value,'1:00');assert.match(c.$('sing-range-status').textContent,/未保存/);
    let sent;c.request=async(path,body)=>{sent={path,body};c.data.songs[0]={...song,playback_range:body.playback_range};};
    c.refresh=async()=>c.showPlaybackRange(c.lyricSong());await c.savePlaybackRange();
    assert.equal(sent.path,'/range');assert.equal(sent.body.playback_range.start_seconds,60);assert.equal(sent.body.playback_range.end_seconds,90);
    assert.equal(c.rangeDirty,false);assert.match(c.$('sing-range-status').textContent,/演唱 0:30/);
    c.showPlaybackRange({id:'two',duration:80});assert.equal(c.$('sing-range-full').checked,true);
});

test('local range audition uses the draft and unsaved edits cannot start a stage performance',async()=>{
    const {controller:c,data}=requestControls(),song={id:'one',duration:200};
    c.data={...data,songs:[song]};c.$('sing-songs').value=song.id;c.showPlaybackRange(song);
    const audio=c.$('sing-audition');audio.currentTime=42;audio.pause=()=>{};let plays=0,posts=0,status;
    audio.play=async()=>plays++;c.markRange('start');c.$('sing-range-end').value='50';
    await c.previewRange();assert.equal(audio.currentTime,42);assert.equal(c.previewRangeEnd,50);assert.equal(plays,1);
    c.request=async()=>posts++;c.status=text=>status=text;await c.play();assert.match(status,/先保存/);assert.equal(posts,0);
    await c.queueSelected();assert.match(c.$('sing-request-result').textContent,/先保存/);assert.equal(posts,0);
    c.$('sing-range-end').value='40';assert.throws(()=>c.rangeDraft(song));
    c.$('sing-range-full').checked=true;assert.equal(c.rangeDraft(song).start_seconds,0);assert.equal(c.rangeDraft(song).end_seconds,null);
});

test('lyric timing drafts survive polling but reset when a different song is selected',()=>{
    const {controller:c,data}=requestControls(),song={id:'one',lyrics_cues:[{at:1,text:'Line'}],lyrics_offset:.5};
    c.data={...data,songs:[song]};c.$('sing-songs').value='one';c.showLyrics(song);
    assert.equal(c.$('sing-lrc-offset').value,.5);assert.equal(c.$('sing-lrc-save').disabled,true);
    c.$('sing-lrc-offset').value='-1.2';c.lyricsOffsetDirty=true;
    c.showLyrics({...song,lyrics_offset:2});assert.equal(c.$('sing-lrc-offset').value,'-1.2');assert.equal(c.$('sing-lrc-save').disabled,false);
    c.showLyrics({id:'two',lyrics_cues:[{at:0,text:'Next'}],lyrics_offset:3});
    assert.equal(c.$('sing-lrc-offset').value,3);assert.equal(c.lyricsOffsetDirty,false);
    c.lyricsError(Error('请另存为 UTF-8'));c.showLyrics({id:'two',lyrics_cues:[{at:0,text:'Next'}],lyrics_offset:3});
    assert.equal(c.$('sing-lrc-status').textContent,'请另存为 UTF-8','polling must not erase an unread upload error');
    c.showLyrics();assert.equal(c.$('sing-lrc-upload').disabled,true);assert.equal(c.$('sing-lrc-save').disabled,true);
    assert.notEqual(c.$('sing-lrc-status').textContent,'请另存为 UTF-8');
});

test('local LRC decoding handles UTF-8, UTF-16 BOM and Chinese encodings without silent replacement',async()=>{
    const {controller:c}=requestControls(),text='[00:01.00]你好';
    assert.equal(await c.readLrcFile(lrcFile(Buffer.from('\ufeff'+text))),text);
    assert.equal(await c.readLrcFile(lrcFile(Buffer.concat([Buffer.from([0xff,0xfe]),Buffer.from(text,'utf16le')]),'SONG.LRC')),text);
    assert.equal(await c.readLrcFile(lrcFile(Buffer.concat([Buffer.from('[00:01.00]'),Buffer.from([0xc4,0xe3,0xba,0xc3])]))),text);
    await assert.rejects(c.readLrcFile(lrcFile(Buffer.from('[00:01.00]\ufffd'))),/UTF-8/);
    await assert.rejects(c.readLrcFile(lrcFile(Buffer.from('[00:01.00]\0'))),/UTF-8/);
    let reads=0;await assert.rejects(c.readLrcFile({name:'too-large.lrc',size:65537,arrayBuffer:async()=>reads++}),/64 KB/);assert.equal(reads,0);
    await assert.rejects(c.readLrcFile(lrcFile(Buffer.from(text),'song.mp3')),/\.lrc/);
    await assert.rejects(c.readLrcFile(lrcFile(Buffer.concat([Buffer.from('[00:01.00]'),Buffer.alloc(44000,0).map((v,i)=>i%2?0xc3:0xba)]))),/64 KB|30000/);
});

test('LRC upload remains available for banned songs and never restarts stage playback',async()=>{
    const {controller:c,data}=requestControls(),song={id:'saved-song',blocked:true,lyrics_cues:[],lyrics_offset:0};
    c.data={...data,songs:[song],playback:{id:'running-performance'}};c.$('sing-songs').value=song.id;c.showLyrics(song);
    assert.equal(c.$('sing-lrc-upload').disabled,false);c.$('sing-lrc-file').files=[lrcFile(Buffer.from('[00:01.20]原创试唱'))];
    const routes=[];c.request=async(path,body)=>{routes.push({path,body});song.lyrics_cues=[{at:1.2,text:'原创试唱'}];song.lyrics_offset=.25;};c.refresh=async()=>c.showLyrics(song);
    await c.uploadLyrics();
    assert.equal(routes.length,1);assert.equal(routes[0].path,'/lyrics');assert.equal(routes[0].body.song_id,song.id);assert.equal(routes[0].body.lrc_text,'[00:01.20]原创试唱');
    assert.equal(c.data.playback.id,'running-performance');assert.equal(c.$('sing-lrc-file').value,'');assert.equal(c.$('sing-lrc-offset').value,.25);
    assert.equal(c.$('sing-lrc-remove').disabled,false);assert.equal(c.lyricsBusy,false);
});

test('lyric offset validation, live updates and removal use only lyric endpoints',async()=>{
    const {controller:c,data}=requestControls(),song={id:'saved-song',lyrics_cues:[{at:1,text:'Line'}],lyrics_offset:0};
    c.data={...data,songs:[song],playback:{id:'keep-playing'}};c.$('sing-songs').value=song.id;c.showLyrics(song);const routes=[];
    c.request=async(path,body)=>{routes.push({path,body});if(path==='/lyrics/settings')song.lyrics_offset=body.offset_seconds;else{song.lyrics_cues=[];song.lyrics_offset=0;}};
    c.refresh=async()=>c.showLyrics(song);
    for(const invalid of ['','Infinity','NaN','-30.1','30.1']){c.$('sing-lrc-offset').value=invalid;await c.saveLyricsOffset();}
    assert.equal(routes.length,0);
    c.$('sing-lrc-offset').value='-0.7';c.lyricsOffsetDirty=true;await c.saveLyricsOffset();
    assert.equal(routes[0].path,'/lyrics/settings');assert.equal(routes[0].body.offset_seconds,-.7);assert.equal(c.data.playback.id,'keep-playing');
    await c.removeLyrics();assert.equal(routes[1].path,'/lyrics/remove');assert.equal(routes[1].body.song_id,song.id);
    assert.equal(c.$('sing-lrc-save').disabled,true);assert.equal(c.$('sing-lrc-remove').disabled,true);assert.equal(c.data.playback.id,'keep-playing');
});

async function requestOutput(){
    const elements=[],events=[],calls=[],state={active:false,voice:{playing:false}};let factory;
    const adapter={
        get active(){return state.active;},get voice(){return state.voice;},
        handle(event){assert.equal(this,adapter);events.push(event);return event.type==='singing_start';},
        dispose(){assert.equal(this,adapter);calls.push('dispose');},
        frame(value){calls.push(value);},unlock(){calls.push('unlock');},setMuted(value){calls.push(value);}
    };
    const box={window:{createPerformanceAdapter:async()=>adapter},StudioModules:{script:async()=>{},registerOutput(id,value){assert.equal(id,'singing');factory=value;}},
        document:{body:{appendChild:element=>elements.push(element)},createElement:()=>({style:{},hidden:false,textContent:'',
            setAttribute(){},remove(){this.removed=true;},set innerHTML(value){throw Error('Queue titles are text, never HTML');}})}};
    vm.createContext(box);vm.runInContext(fs.readFileSync('frontend/static/modules/singing-output.js','utf8'),box);
    return {module:await factory({}),adapter,state,elements,events,calls};
}

test('song-request output preserves live performance getters and forwards its lifecycle methods',async()=>{
    const f=await requestOutput();assert.equal(f.module,f.adapter);
    assert.equal(typeof Object.getOwnPropertyDescriptor(f.module,'active').get,'function');
    assert.equal(typeof Object.getOwnPropertyDescriptor(f.module,'voice').get,'function');
    assert.equal(f.module.active,false);f.state.active=true;f.state.voice={playing:true,analyser:{}};
    assert.equal(f.module.active,true);assert.equal(f.module.voice,f.state.voice);
    const event={type:'singing_start',performance:{id:'take-one'}};assert.equal(f.module.handle(event),true);assert.equal(f.events[0],event);
    const params={};f.module.frame(params);f.module.unlock();f.module.setMuted(true);
    assert.equal(f.calls[0],params);assert.deepEqual(f.calls.slice(1),['unlock',true]);
});

test('song-request badge uses literal titles, updates from snapshots and clears on profile change or disposal',async()=>{
    const f=await requestOutput(),title='<img src=x onerror=alert(1)>';
    const update={type:'song_requests_update',requests:{queue:[{title},{title:'Next song'}]}};
    assert.equal(f.module.handle(update),false);assert.equal(f.events.at(-1),update);assert.equal(f.elements.length,1);
    const badge=f.elements[0];assert.equal(badge.hidden,false);assert.equal(badge.textContent,'♫ 待唱 2 首\n下一首 · '+title);
    const snapshot={type:'status',song_requests:{queue:[{title:'小幸运'}]}};f.module.handle(snapshot);
    assert.equal(badge.textContent,'♫ 待唱 1 首\n下一首 · 小幸运');assert.equal(f.elements.length,1);assert.equal(f.events.at(-1),snapshot);
    f.module.handle({type:'singing_stop'});assert.equal(badge.hidden,false);
    f.module.handle({type:'profile_changed'});assert.equal(badge.hidden,true);assert.equal(badge.textContent,'');
    f.module.handle({type:'song_requests_update',requests:{queue:[]}});assert.equal(badge.hidden,true);
    f.module.dispose();assert.equal(badge.removed,true);assert.equal(f.calls.at(-1),'dispose');
});
