const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function runtime(){
    const box={window:{addEventListener(){}},console,Map,AbortSignal,setTimeout,clearTimeout};vm.createContext(box);
    vm.runInContext(fs.readFileSync('frontend/static/module-host.js','utf8'),box);return box;
}
test('new stage modules receive events, snapshots and frame hooks without changing the client',async()=>{
    const box=runtime(),registry=box.window.StudioModules;let disposed=false,frames=0;
    registry.manifest=async()=>({modules:[{id:'story',output:'/static/modules/story-output.js',presentation:{pose:'think'}}]});
    registry.script=async()=>{};registry.registerOutput('story',()=>({active:true,voice:{playing:true},handle:e=>e.type==='story_start',frame:()=>frames++,dispose:()=>disposed=true}));
    const host=new box.window.StageModuleHost({});await host.init();
    assert.equal(host.presentation('story').pose,'think');assert.equal(host.handle({type:'story_start'}),true);
    assert.equal(host.handle({type:'audio_segment'}),true);assert.equal(host.handle({type:'status'}),false);
    assert.equal(host.voice.playing,true);host.frame({},.1);assert.equal(frames,1);host.dispose();assert.equal(disposed,true);
});
test('singing adapter uses its own presentation and restores conversation after stopping',async()=>{
    const box=runtime(),registry=box.window.StudioModules,actions=[],faces=[],car=[];let instance;
    registry.script=async()=>{};
    box.SingingPlayer=class{constructor(options){this.options=options;instance=this;this.position=1;this.timelinePlaying=true;}load(p){this.performance=p;if(p)this.options.onStart(p);}stop(){this.performance=null;this.options.onStop();}dispose(){} };
    vm.runInContext(fs.readFileSync('frontend/static/modules/performance-output.js','utf8'),box);
    vm.runInContext(fs.readFileSync('frontend/static/modules/singing-output.js','utf8'),box);
    const client={isPreview:false,muted:false,stopAudio(){},modules:{presentation:()=>({motion_strength:.4})},
        carScene:{setSinging:(...args)=>car.push(args)},renderer:{setAction:a=>actions.push(a),face:{setExpression:(...args)=>faces.push(args)}},
        music:{setSpeaking(){}},subtitles:{begin(){},show(){},finish(){}},voiceEnvelope:{reset(){}}};
    const module=await registry.outputs.get('singing')(client);
    module.handle({type:'singing_start',performance:{song:{title:'Original'},bpm:80,presentation:{pose:'shy',expression:'happy',motion_strength:.2}}});
    assert.equal(actions.at(-1).name,'shy');assert.equal(actions.at(-1).intensity,.2);assert.equal(faces.at(-1)[0],'happy');assert.equal(car.at(-1)[1],'happy');
    const params={};module.frame(params);assert.equal(params.moduleSway.amount,.2);assert.equal(module.active,true);
    module.handle({type:'singing_stop'});assert.equal(module.active,false);assert.equal(actions.at(-1).name,'idle');assert.equal(car.at(-1)[0],false);
});
test('module loader rejects remote scripts and traversal',async()=>{
    const registry=runtime().window.StudioModules;
    await assert.rejects(registry.script('https://example.com/plugin.js'));
    await assert.rejects(registry.script('/static/../unsafe.js'));
});

test('module scene selector edits a draft and submits only the module look',async()=>{
    const elements=new Map();
    const element=id=>{if(!elements.has(id))elements.set(id,{value:'',checked:false,replaceChildren(...items){this.options=items;}});return elements.get(id);};
    const look={pose:'idle',expression:'singing',motion_strength:.35,expression_enabled:true,expression_strength:.55,scene_preset:'singing-room'};
    const manifest={active_profile:'astra',revision:7,modules:[{id:'singing',title:'Songs',poses:['idle','nod'],expressions:['singing','happy'],presentation:look}]};
    const box=runtime();let posted;
    box.document={getElementById:element};box.Option=function(label,value){this.label=label;this.value=value;};
    box.fetch=async(url,options)=>{posted={url,...options};return {ok:true,json:async()=>manifest};};
    vm.runInContext('this.Shelf=ModuleShelf',box);
    const shelf=Object.create(box.Shelf.prototype);shelf.dialog={showModal(){this.open=true;}};
    box.window.StudioModules.manifest=async()=>manifest;
    await shelf.edit('singing');
    assert.equal(element('module-scene-preset').value,'singing-room');assert.equal(shelf.dialog.open,true);
    element('module-scene-preset').value='inherit';assert.equal(look.scene_preset,'singing-room');assert.equal(posted,undefined);
    await shelf.save();
    const payload=JSON.parse(posted.body);
    assert.equal(posted.url,'/api/modules/singing/presentation');assert.equal(posted.headers['X-Studio-API'],'1');
    assert.equal(payload.profile_id,'astra');assert.equal(payload.revision,7);assert.equal(payload.presentation.scene_preset,'inherit');
    assert.equal(payload.scene,undefined);assert.equal(payload.voice,undefined);
    assert.equal(look.scene_preset,'singing-room','unsaved manifest remains untouched');
});
