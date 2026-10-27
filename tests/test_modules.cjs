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
    box.SingingPlayer=class{constructor(options){this.options=options;instance=this;this.position=1;}load(p){this.performance=p;if(p)this.options.onStart(p);}stop(){this.performance=null;this.options.onStop();}dispose(){} };
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
