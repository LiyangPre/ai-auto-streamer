const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {test}=require('node:test');
function fixture(options={}) {
    const calls={lines:0,clouds:0,clears:0,motes:0},ctx={
        createRadialGradient(){return {addColorStop(){}};},fillRect(){},setTransform(){},
        save(){},restore(){},clearRect(){calls.clears++;},beginPath(){},moveTo(){},lineTo(){},
        stroke(){calls.lines++;},drawImage(){calls.clouds++;},arc(){},fill(){calls.motes++;}};
    const box={window:{addEventListener(){},removeEventListener(){},innerWidth:1920,innerHeight:1080,devicePixelRatio:3},
        document:{createElement(){return {getContext(){return ctx;}};}}};vm.createContext(box);
    vm.runInContext(fs.readFileSync('frontend/static/stage-scene.js','utf8')+'\nthis.Scene=StageScene;',box);
    const style=()=>({setProperty(k,v){this[k]=v;}});
    const canvas={clientWidth:1280,clientHeight:720,getContext(){return ctx;}},layer={style:style()},character={style:{}},companions={},bottomBlur={style:style()};
    const scene=new box.Scene(layer,canvas,character,{companions,bottomBlur,...options});
    return {Scene:box.Scene,scene,calls,canvas,layer,character,companions,bottomBlur};
}
test('placement is relative and malformed values cannot inject CSS or escape limits',()=>{
    const {Scene,character,scene}=fixture();
    scene.setSettings({character_x:-20,character_y:5,character_scale:1.3});
    assert.equal(character.style.transform,'translate(-20%, 5%) scale(1.3)');
    assert.equal(Scene.normalize({character_scale:Infinity}).character_scale,1);
    assert.equal(Scene.normalize({character_x:1000}).character_x,45);
    assert.equal(Scene.normalize({background:'url(evil)',effect:'video'}).background,'moonlit');
    assert.equal(Scene.normalize({character_x:'0);url(evil)'}).character_x,0);
});

test('uploaded PNG uses a local media ID and retains reusable dolls and framing',()=>{
    const {Scene,scene,layer,companions,character}=fixture();const id='a'.repeat(32);
    scene.setSettings({background:'custom',background_asset:id,companions:true,character_scale:.8});
    assert.equal(layer.style['--scene-art'],`url("/media/${id}.png")`);
    assert.equal(companions.hidden,false);assert.equal(character.style.visibility,'');
    assert.equal(character.style.transform,'translate(0%, 0%) scale(0.8)');
    for(const value of ['../../secret','https://example.com/image.png','a'.repeat(32)+'")']) {
        const safe=Scene.normalize({background_asset:value,music_asset:value});
        assert.equal(safe.background_asset,null);assert.equal(safe.music_asset,null);
    }
});
test('weather work is bounded, pauses when hidden, and uses a capped pixel ratio',()=>{
    const {scene,calls,canvas}=fixture();
    assert.equal(canvas.width,1600);assert.equal(canvas.height,900);
    scene.setSettings({effect:'rain',weather_amount:1});calls.lines=0;scene.elapsed=0;
    for(let i=0;i<60;i++)scene.render(1/60);
    assert.ok(calls.lines<=150*21&&calls.lines>=150*18);
    const before=calls.lines;scene.render(1,true);assert.equal(calls.lines,before);
    assert.equal(scene.drops.length,150);
});
test('transparent, still, zero intensity and reduced motion render no moving weather',()=>{
    const {scene,calls,layer}=fixture();
    for(const settings of [{background:'transparent',effect:'rain'},{effect:'none'},{effect:'rain',weather_amount:0}]) {
        scene.setSettings(settings);const before=calls.lines+calls.clouds;
        scene.render(.1);assert.equal(calls.lines+calls.clouds,before);
    }
    scene.setSettings({background:'transparent'});assert.equal(layer.hidden,true);
    const reduced=fixture({reducedMotion:true});assert.equal(reduced.calls.clouds,0);
    reduced.scene.render(.1);assert.equal(reduced.calls.clouds,0);
    const overlay=fixture({transparent:true});overlay.scene.setSettings({background:'moonlit'});
    assert.equal(overlay.layer.hidden,true,'explicit transparent output must remain transparent');
});
test('changing weather clears stale particles without changing character framing',()=>{
    const {scene,calls,character}=fixture();
    const framing={character_x:12,character_y:3,character_scale:1.1};
    scene.setSettings({...framing,effect:'rain'});const old=calls.clears;
    scene.setSettings({...framing,effect:'clouds'});
    assert.ok(calls.clears>old);assert.equal(character.style.transform,'translate(12%, 3%) scale(1.1)');
});

test('local backdrops, optional companions and bottom blur preserve transparent output',()=>{
    const {scene,layer,companions,bottomBlur,Scene}=fixture();
    scene.setSettings({background:'palace',bottom_blur:7,companions:true});
    assert.equal(layer.style['--scene-art'],'url("/static/scenes/royal-palace.png")');
    assert.equal(companions.hidden,false);assert.equal(bottomBlur.hidden,false);
    assert.equal(bottomBlur.style['--bottom-blur'],'7px');
    scene.setSettings({background:'garden',bottom_blur:0,companions:false});
    assert.equal(layer.style['--scene-art'],'url("/static/scenes/rose-garden.png")');
    assert.equal(companions.hidden,true);assert.equal(bottomBlur.hidden,true);
    scene.setSettings({background:'transparent',companions:true,bottom_blur:12});
    assert.equal(companions.hidden,true);assert.equal(bottomBlur.hidden,true);
    assert.equal(Scene.normalize({bottom_blur:1000}).bottom_blur,12);
    const overlay=fixture({transparent:true});
    assert.equal(overlay.companions.hidden,true);assert.equal(overlay.bottomBlur.hidden,true);
});

test('palace motes remain bounded and pause in hidden or reduced-motion views',()=>{
    const {scene,calls}=fixture();scene.setSettings({effect:'sparkles',weather_amount:1});
    calls.motes=0;scene.elapsed=0;
    for(let i=0;i<60;i++)scene.render(1/60);
    assert.ok(calls.motes<=40*21&&calls.motes>=40*18);
    const before=calls.motes;scene.render(.1,true);assert.equal(calls.motes,before);
    scene.reducedMotion=true;scene.render(.1);assert.equal(calls.motes,before);
});

test('car scene locks contact geometry, hides standing companions, and restores prior framing',()=>{
    const {scene,character,companions,layer,calls}=fixture();
    const framing={character_x:12,character_y:7,character_scale:1.3};
    scene.setSettings({...framing,background:'car',effect:'rain'});
    assert.equal(layer.hidden,true);assert.equal(companions.hidden,true);
    assert.equal(character.style.visibility,'hidden');assert.equal(character.style.transform,'none');
    const count=calls.lines+calls.clouds;scene.render(.1);assert.equal(calls.lines+calls.clouds,count);
    scene.setSettings({...framing,background:'palace'});
    assert.equal(character.style.visibility,'');assert.equal(character.style.transform,'translate(12%, 7%) scale(1.3)');
    assert.equal(companions.hidden,false);assert.equal(layer.hidden,false);
    const transparent=fixture({transparent:true});transparent.scene.setSettings({background:'car'});
    assert.equal(transparent.scene.car,false,'transparent capture must never contain the car artwork');
});

test('music room owns character and dolls without leaking weather or changing saved framing',()=>{
    const {scene,character,companions,layer,calls}=fixture();
    const settings={background:'singing-room',effect:'rain',character_x:8,character_y:-3,character_scale:1.2,music_volume:.13};
    scene.setSettings(settings);
    assert.equal(scene.settings.background,'singing-room');assert.equal(scene.singingRoom,true);
    assert.equal(scene.integrated,true);assert.equal(scene.car,false);
    assert.equal(character.style.visibility,'hidden');assert.equal(character.style.transform,'none');
    assert.equal(companions.hidden,true);assert.equal(layer.hidden,true);
    const before=calls.lines+calls.clouds+calls.motes;
    scene.render(.1);assert.equal(calls.lines+calls.clouds+calls.motes,before);
    assert.equal(settings.background,'singing-room','input settings must not be mutated');
    assert.equal(scene.settings.music_volume,.13);
    scene.setSettings({...settings,background:'garden'});
    assert.equal(scene.singingRoom,false);assert.equal(scene.integrated,false);
    assert.equal(character.style.visibility,'');assert.equal(character.style.transform,'translate(8%, -3%) scale(1.2)');
    assert.equal(companions.hidden,false);assert.equal(layer.hidden,false);
    const transparent=fixture({transparent:true});transparent.scene.setSettings(settings);
    assert.equal(transparent.scene.singingRoom,false);assert.equal(transparent.layer.hidden,true);
});
