const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const policy={enabled:true,narrate_scene_changes:true,threshold:6,window_seconds:120,trigger_cooldown:300,
    viewer_cooldown:20,minimum_confidence:.8,selection_seconds:90};
const snapshot=(profile='astra',revision=1,extra={})=>({profile_id:profile,revision,policy:{...policy,...extra},
    state:{counts:{singing:2,story:1},awaiting_song:false,cooldown_remaining:0}});

function fixture(){
    const elements=new Map(),events={},calls=[],timers=new Set();
    const el=id=>{
        if(!elements.has(id))elements.set(id,{value:'',checked:false,textContent:'',disabled:false,handlers:{},
            addEventListener(name,fn){this.handlers[name]=fn;},reportValidity(){return true;},classList:{toggle(){}}});
        return elements.get(id);
    };
    const box={module:{exports:{}},console,Event:class{constructor(type){this.type=type;}},AbortSignal:{timeout:()=>({})},
        document:{hidden:false,getElementById:el,addEventListener(){}},
        window:{addEventListener(name,fn){events[name]=fn;},dispatchEvent(){}},
        setInterval(fn){timers.add(fn);return fn;},clearInterval(fn){timers.delete(fn);},
        fetch:(url,options)=>new Promise(resolve=>calls.push({url,options,respond(data,ok=true){resolve({ok,json:async()=>data});}}))};
    vm.createContext(box);vm.runInContext(fs.readFileSync('frontend/static/interaction-controls.js','utf8'),box);
    const ui=new box.module.exports.InteractionControls();
    return {ui,el,calls,events,timers};
}

test('local polling carries security header and never overwrites a dirty draft or revision',async()=>{
    const f=fixture();assert.equal(f.calls[0].url,'/api/interactions');
    assert.equal(f.calls[0].options.headers['X-Studio-API'],'1');
    f.calls[0].respond(snapshot());await tick();
    f.el('interaction-threshold').value='9';f.el('interaction-form').handlers.input();
    const pending=f.ui.load(),next=snapshot('astra',2);next.state.counts.singing=5;
    f.calls[1].respond(next);await pending;
    assert.equal(f.el('interaction-threshold').value,'9');assert.equal(f.ui.collect().revision,1);
    assert.equal(f.el('interaction-singing-count').textContent,'5');
    assert.equal(f.ui.collect().policy.minimum_confidence,.8,'unexposed policy field is preserved');
});

test('switching characters replaces a draft rather than saving it to another character',async()=>{
    const f=fixture();f.calls[0].respond(snapshot());await tick();
    f.el('interaction-threshold').value='10';f.el('interaction-form').handlers.input();
    const pending=f.ui.load();f.calls[1].respond(snapshot('claude',3,{threshold:8}));await pending;
    assert.equal(f.ui.collect().profile_id,'claude');assert.equal(f.ui.collect().revision,3);
    assert.equal(f.ui.collect().policy.threshold,8);assert.equal(f.ui.dirty,false);
});

test('an old GET finishing after save cannot roll back the new policy',async()=>{
    const f=fixture();f.calls[0].respond(snapshot());await tick();
    f.el('interaction-threshold').value='8';f.el('interaction-form').handlers.input();
    const oldLoad=f.ui.load(),saving=f.ui.save();
    assert.equal(f.calls[2].options.method,'POST');assert.equal(JSON.parse(f.calls[2].options.body).policy.threshold,8);
    f.calls[2].respond(snapshot('astra',2,{threshold:8}));await saving;
    f.calls[1].respond(snapshot('astra',1));await oldLoad;
    assert.equal(f.ui.collect().revision,2);assert.equal(f.ui.collect().policy.threshold,8);
});

test('failed save preserves draft and stale revision until explicit reload',async()=>{
    const f=fixture();f.calls[0].respond(snapshot());await tick();
    f.el('interaction-threshold').value='10';f.el('interaction-form').handlers.input();
    const saving=f.ui.save();f.calls[1].respond({detail:'角色设置已改变'},false);await saving;
    assert.equal(f.ui.dirty,true);assert.equal(f.ui.collect().revision,1);assert.equal(f.ui.collect().policy.threshold,10);
    assert.match(f.el('interaction-status').textContent,/重新载入/);assert.equal(f.el('interaction-fields').disabled,false);
});

test('closing the page stops polling and ignores in-flight responses',async()=>{
    const f=fixture();assert.equal(f.timers.size,1);f.ui.dispose();assert.equal(f.timers.size,0);
    f.calls[0].respond(snapshot());await tick();assert.equal(f.ui.editing,null);
    await f.ui.load();assert.equal(f.calls.length,1);
});
