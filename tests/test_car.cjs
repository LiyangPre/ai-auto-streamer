const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {test}=require('node:test');
function fixture(options={}) {
    const draws=[],transforms=[];
    const ctx={clearRect(){},drawImage(...args){draws.push(args);},setTransform(...args){transforms.push(args);},
        save(){},restore(){},translate(){},rotate(){},scale(){},transform(){},beginPath(){},moveTo(){},quadraticCurveTo(){},
        bezierCurveTo(){},lineTo(){},ellipse(){},stroke(){},fill(){},clip(){},closePath(){},fillRect(){},
        createRadialGradient(){return {addColorStop(){}};}};
    const canvas={width:0,height:0,getContext:()=>ctx},img={},status={};
    const layer={clientWidth:720,clientHeight:540,dataset:{},style:{setProperty(){}},querySelector:s=>s==='.car-face'?canvas:s==='img'?img:status};
    const box={window:{addEventListener(){},removeEventListener(){},devicePixelRatio:1},
        getComputedStyle:()=>({width:'714px',height:'535.5px'}),console};vm.createContext(box);
    for(const name of ['character-motion','character-face','car-scene'])vm.runInContext(fs.readFileSync(`frontend/static/${name}.js`,'utf8'),box);
    const scene=new box.window.CarScene(layer,options);scene.loading=Promise.resolve();
    scene.ready=true;scene.plate={id:'clean-face'};scene.eyes=[{bounds:[395,270,115,71],canvas:{id:'left'}},{bounds:[557,248,108,73],canvas:{id:'right'}}];
    return {scene,canvas,draws,transforms,layer,CarScene:box.window.CarScene};
}
test('car expression transitions and blink preserve face continuity without moving the body',()=>{
    const {scene}=fixture();scene.setExpression('angry');const first=scene.sample(1/30);
    assert.ok(first.tilt>0&&first.tilt<.1);assert.ok(first.open>.65&&first.open<1);
    for(let i=0;i<50;i++)scene.sample(1/30);
    assert.ok(scene.current.tilt>.099);scene.setExpression('sad');const next=scene.sample(1/30);
    assert.ok(next.tilt<.1&&next.tilt>-.065,'new expression must blend, never snap');
    scene.setExpression('blink');let closed=0;
    for(let i=0;i<20;i++)closed=Math.max(closed,scene.sample(1/60).blink);
    assert.ok(closed>.99);assert.equal(scene.sample(1/60).blink,0);
    scene.setExpression('invalid');assert.equal(scene.expression,'normal');
});
test('active output keeps a 1440x1080 facial canvas and draws only local face patches',()=>{
    const {scene,canvas,draws,transforms}=fixture();scene.setActive(true,{dim:.1});
    assert.equal(canvas.width,1440);assert.equal(canvas.height,1080);
    assert.equal(transforms.at(-1)[0],1440/1448);assert.equal(transforms.at(-1)[3],1080/1086);
    scene.render({mouthOpen:.7,mouthRound:.2},1/30);
    assert.ok(draws.length>=3);assert.ok(draws.every(d=>['clean-face','left','right'].includes(d[0].id)),
        'no body, wheel or companion image may be transformed');
    const count=draws.length;scene.render({},1/30,true);assert.equal(draws.length,count);
    scene.setActive(false);scene.render({},1/30);assert.equal(draws.length,count);
});
test('lip sync uses the voice envelope only and transient expressions relax',()=>{
    const {scene}=fixture();scene.setActive(true);const openings=[];scene.drawMouth=open=>openings.push(open);
    scene.setAction({name:'laugh',mouthOpen:1});scene.render({},1/30);
    scene.render({mouthOpen:.75},1/30);scene.render({mouthOpen:0},1/30);scene.render({mouthOpen:NaN},1/30);
    assert.deepEqual(openings,[0,.75,0,0]);
    for(let i=0;i<300;i++)scene.sample(1/30);
    assert.ok(Math.abs(scene.current.smile-.22)<.001);
});

test('singing holds a symmetric expression and releases smoothly without moving body contacts',()=>{
    const {scene,draws}=fixture();scene.setActive(true);scene.setExpression('smug');
    for(let i=0;i<60;i++)scene.sample(1/30);
    scene.setSinging(true);for(let i=0;i<400;i++)scene.sample(1/30);
    assert.ok(Math.abs(scene.current.asym)<.00001);assert.ok(Math.abs(scene.current.open-.42)<.001);
    scene.render({mouthOpen:.8},1/30);assert.ok(draws.every(d=>['clean-face','left','right'].includes(d[0].id)));
    scene.setSinging(false);const first=scene.sample(1/30);assert.ok(first.open>.42&&first.open<1);
});

test('car breath moves chest cloth visibly but pins face, wheel, wrists, dolls and seatbelt anchors',()=>{
    const {CarScene:C}=fixture(),anchors=[[542,378],[395,270],[665,440],[1300,650],[250,850],
        [1130,480],[1340,480],[780,570],[550,1030],[1050,830],[0,1086],[1448,0]];
    for(const breath of [-1,-.5,0,.5,1])for(const point of anchors)
        assert.deepEqual([...C.breathPoint(point,breath)],point,'physical contact may not move');
    assert.deepEqual([...C.breathPoint([660,725],1)],[660,719.5]);
    assert.deepEqual([...C.breathPoint([660,725],-1)],[660,730.5]);
    assert.deepEqual([...C.breathPoint([660,725],NaN)],[660,725]);
});

test('bounded breathing mesh keeps a continuous torso without flipped or excessively stretched triangles',()=>{
    const {CarScene:C}=fixture(),mesh=C.makeBreathMesh(),area=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(c[0]-a[0])*(b[1]-a[1]);
    assert.ok(mesh.triangles.length>0&&mesh.triangles.length<=360);
    for(const breath of [-1,1])for(const ids of mesh.triangles){
        const source=ids.map(i=>mesh.vertices[i].point),dest=ids.map(i=>C.breathPoint(mesh.vertices[i].point,breath,mesh.vertices[i].weight)),
            ratio=area(...dest)/area(...source);
        assert.ok(ratio>.92&&ratio<1.08,`cloth triangle must remain physical: ${ratio}`);
    }
});

test('idle car breath continues without speech and freezes while hidden, reduced or disabled',()=>{
    const {scene,draws}=fixture();scene.setActive(true);scene.art={id:'car-art'};
    const openings=[];scene.drawMouth=open=>openings.push(open);
    for(let i=0;i<12;i++)scene.render({},.1,false);
    assert.ok(scene.breath>.99);assert.ok(draws.some(d=>d[0]===scene.art));assert.ok(openings.every(x=>x===0));
    assert.ok(draws.filter(d=>d[0]===scene.art).every(d=>d.length===9&&d[3]<100&&d[4]<100),
        'breathing only draws cached local triangle crops, never a full car transform');
    const time=scene.breathClock,count=draws.length;
    scene.render({},1,true);assert.equal(scene.breathClock,time);assert.equal(draws.length,count);
    scene.reducedMotion=true;scene.render({mouthOpen:.6},.1);assert.equal(scene.breathClock,time);assert.equal(scene.breath,0);
    assert.equal(openings.at(-1),.6,'reduced motion retains real voice lips');
    scene.reducedMotion=false;scene.setBreathing(false);scene.render({},.1);assert.equal(scene.breath,0);assert.equal(scene.breathClock,time);
    scene.setBreathing(true);scene.render({},.1);assert.ok(scene.breath>.9);
    scene.dispose();const stopped=draws.length;scene.render({},.1);assert.equal(draws.length,stopped);
});
