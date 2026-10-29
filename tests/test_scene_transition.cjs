const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {SceneTransition,SceneAssets}=require('../frontend/static/scene-transition.js');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(options={}){
    const parts=Object.fromEntries(['title','message','retry','dismiss'].map(name=>[name,{hidden:true,listeners:{},
        addEventListener(event,fn){this.listeners[event]=fn;},removeEventListener(event){delete this.listeners[event];}}]));
    const layer={hidden:true,dataset:{},attributes:{},setAttribute(name,value){this.attributes[name]=value;},
        querySelector:selector=>parts[selector.slice(12,-1)]};
    const transition=new SceneTransition(layer,{reducedMotion:true,afterPaint:()=>Promise.resolve(),...options});
    return {transition,layer,parts};
}

test('transition retains current scene until prepared and only hides after a painted commit',async()=>{
    const ready=deferred(),painted=deferred(),{transition:t,layer}=fixture({afterPaint:()=>painted.promise});let scene='car';
    const changing=t.change({key:'singing',background:'singing-room',prepare:()=>ready.promise,commit:()=>{scene='singing';}});
    assert.equal(layer.hidden,false);assert.equal(layer.dataset.state,'loading');assert.equal(scene,'car');
    ready.resolve();await tick();assert.equal(scene,'singing');assert.equal(layer.hidden,false);
    painted.resolve();assert.deepEqual(await changing,{ready:true});assert.equal(layer.hidden,true);t.dispose();
});

test('stale scene preparation cannot commit over a newer choice',async()=>{
    const slow=deferred(),{transition:t,layer}=fixture();const commits=[];
    const old=t.change({key:'old',prepare:()=>slow.promise,commit:()=>commits.push('old')});
    const current=t.change({key:'new',prepare:async()=>{},commit:()=>commits.push('new')});
    assert.deepEqual(await old,{cancelled:true});await current;slow.resolve();await tick();
    assert.deepEqual(commits,['new']);assert.equal(layer.hidden,true);t.dispose();
});

test('repeated pending scene deduplicates loading but uses latest settings on commit',async()=>{
    const ready=deferred(),{transition:t}=fixture();let loads=0,settings=0;
    const first=t.change({key:'same',prepare:()=>{loads++;return ready.promise;},commit:()=>{settings=1;}});
    const second=t.change({key:'same',prepare:()=>{loads++;},commit:()=>{settings=2;}});
    assert.equal(first,second);ready.resolve();await first;assert.equal(loads,1);assert.equal(settings,2);
    await t.change({key:'same',prepare:()=>{throw new Error('must not reload');},commit:()=>{settings=3;}});
    assert.equal(settings,3);t.dispose();
});

test('failed scene keeps previous output and offers a retry instead of a permanent curtain',async()=>{
    const {transition:t,layer,parts}=fixture();let scene='car',fail=true;
    const request={key:'singing',background:'singing-room',prepare:async()=>{if(fail)throw new Error('unavailable');},commit:()=>{scene='singing';}};
    assert.deepEqual(await t.change(request),{ready:false});assert.equal(scene,'car');assert.equal(layer.dataset.state,'error');
    assert.equal(parts.retry.hidden,false);assert.equal(parts.dismiss.hidden,false);
    fail=false;parts.retry.listeners.click();await t.pending.promise;
    assert.equal(scene,'singing');assert.equal(layer.hidden,true);t.dispose();
});

test('timeout is bounded, dismissible, and late load cannot change the output',async()=>{
    const blocked=deferred(),{transition:t,layer,parts}=fixture({timeoutMs:15});let commits=0;
    assert.deepEqual(await t.change({key:'blocked',prepare:()=>blocked.promise,commit:()=>commits++}),{ready:false});
    assert.equal(layer.dataset.state,'error');parts.dismiss.listeners.click();assert.equal(layer.hidden,true);
    blocked.resolve();await tick();assert.equal(commits,0);t.dispose();
});

test('transparent output never receives an opaque frame, even on error',async()=>{
    const {transition:t,layer}=fixture();await t.change({key:'transparent',transparent:true,prepare:async()=>{},commit(){}});
    assert.equal(layer.hidden,true);
    await t.change({key:'transparent-error',transparent:true,prepare:async()=>{throw Error();},commit(){}});
    assert.equal(layer.hidden,true);t.dispose();
});

test('disposing cancels pending commits and removes retry listeners',async()=>{
    const {transition:t,parts,layer}=fixture();let commits=0;
    const pending=t.change({key:'x',prepare:()=>new Promise(()=>{}),commit:()=>commits++});t.dispose();
    assert.deepEqual(await pending,{cancelled:true});assert.equal(commits,0);assert.equal(layer.hidden,true);
    assert.equal(parts.retry.listeners.click,undefined);
});

test('asset readiness includes decode and shares a local image request',async()=>{
    const decoded=deferred();let loads=0;
    class Image {set src(value){loads++;this.path=value;queueMicrotask(()=>this.onload());}decode(){return decoded.promise;}}
    const assets=new SceneAssets({ImageClass:Image,timeoutMs:50});let ready=false;
    const one=assets.image('/static/scenes/scene.png');assert.equal(one,assets.image('/static/scenes/scene.png'));
    one.then(()=>{ready=true;});await tick();assert.equal(ready,false);decoded.resolve();await one;
    assert.equal(ready,true);assert.equal(loads,1);
    await assert.rejects(assets.image('https://unrelated.example/art.png'));
    await assert.rejects(assets.image('/static/../private.png'));
});

test('failed asset is evicted so a retry can load it successfully',async()=>{
    let fail=true;
    class Image {set src(value){queueMicrotask(()=>fail?this.onerror():this.onload());}async decode(){}}
    const assets=new SceneAssets({ImageClass:Image,timeoutMs:50});await assert.rejects(assets.image('/media/abc.png'));
    fail=false;assert.ok(await assets.image('/media/abc.png'));
});

test('many custom scenes cannot retain an unbounded decoded-image cache',async()=>{
    let loads=0;
    class Image {set src(value){loads++;queueMicrotask(()=>this.onload());}async decode(){}}
    const assets=new SceneAssets({ImageClass:Image,timeoutMs:50,cacheLimit:4});
    for(let i=0;i<10;i++)await assets.image('/media/'+i+'.png');
    assert.equal(assets.images.size,4);await assets.image('/media/9.png');assert.equal(loads,10);
    await assets.image('/media/0.png');assert.equal(loads,11);assert.equal(assets.images.size,4);
});

test('implicit song selection temporarily chooses music room; finite performance and explicit URL take precedence',()=>{
    const box={window:{addEventListener(){}},console,URLSearchParams,location:{search:''}};vm.createContext(box);
    vm.runInContext(fs.readFileSync('frontend/static/app.js','utf8')+';this.Client=AIStreamerClient',box);
    vm.runInContext('this.StageScene={normalize:value=>({...value})};',box);
    const client=Object.create(box.Client.prototype);let committed;
    client.sharedScene={background:'car'};client.commitScene=value=>{committed=value;};
    client.modules={handle:()=>false,presentation:id=>({scene_preset:id==='story'?'garden':'inherit'}),active:null};
    client.handleMessage({type:'audience_interaction_update',state:{phase:'choosing_song',scene_preset:'singing-room'}});
    assert.equal(committed.background,'singing-room');assert.equal(client.sharedScene.background,'car');
    client.modules.active={moduleId:'story'};client.refreshModuleScene();assert.equal(committed.background,'garden');
    client.modules.active=null;client.sceneOverride='palace';client.refreshModuleScene();assert.equal(committed.background,'palace');
    client.sceneOverride=null;client.handleMessage({type:'audience_interaction_update',state:{phase:'chat',scene_preset:null}});
    assert.equal(committed.background,'car');
    client.handleMessage({type:'status',audience_interaction:{phase:'choosing_song',scene_preset:'singing-room'}});
    assert.equal(committed.background,'singing-room','reconnect snapshot restores song-selection room');
    client.handleMessage({type:'status',healthy:true});assert.equal(committed.background,'singing-room');
    client.handleMessage({type:'status',audience_interaction:{phase:'chat',scene_preset:null}});
    assert.equal(committed.background,'car');
});

test('bfcache restoration reconstructs the disposed renderers with one clean reload',()=>{
    let reloads=0;const box={window:{addEventListener(){},location:{reload(){reloads++;}}}};vm.createContext(box);
    vm.runInContext(fs.readFileSync('frontend/static/app.js','utf8')+';this.Client=AIStreamerClient',box);
    const client=Object.create(box.Client.prototype);
    client.restorePage({persisted:false});assert.equal(reloads,0);
    client.restorePage({persisted:true});assert.equal(reloads,1);
});

test('silent scene rehearsal loads the real blink dependencies in browser script order',()=>{
    const html=fs.readFileSync('frontend/static/previews/scene-transition.html','utf8'),
        scripts=[...html.matchAll(/<script src="(\/static\/[^"?]+)(?:\?[^\"]*)?"/g)].map(match=>match[1]);
    const box={window:{},console,setTimeout,clearTimeout};vm.createContext(box);
    for(const script of scripts.filter(path=>!path.includes('/previews/')))
        vm.runInContext(fs.readFileSync('frontend'+script,'utf8'),box,{filename:script});
    const face=new box.window.CharacterFace(()=>0);let closed=0;
    for(let frame=0;frame<300;frame++)closed=Math.max(closed,face.sample(1/30).blink);
    assert.ok(closed>.99,'the first automatic blink must run without stopping the scene rehearsal');
    assert.equal(typeof box.window.CarScene,'function');assert.equal(typeof box.window.SingingScene,'function');
});
