const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {test}=require('node:test');
const {ART,LIMITS,SEAT,applyBodyPose,DEFAULT_CALIBRATION:C,skinWeights,deformPoint,timelinePose,buildMesh,pairedFace,rotate,drawTriangle}=require('../frontend/static/singing-scene.js');
const distance=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1]);
const area=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(c[0]-a[0])*(b[1]-a[1]);
const joints=pose=>({head:pose.head,mic:pose.mic,body:pose.body});

function fixture({preview=false,reducedMotion=false,breathing=true}={}) {
    const draws=[],transforms=[],calls={reads:0,images:0},listeners=new Set();
    const context=()=>({clearRect(){},drawImage(...args){draws.push(args);},setTransform(...args){transforms.push(args);},
        save(){},restore(){},translate(){},rotate(){},scale(){},transform(){},beginPath(){},moveTo(){},lineTo(){},
        quadraticCurveTo(){},bezierCurveTo(){},ellipse(){},stroke(){},fill(){},clip(){},closePath(){},fillRect(){},rect(){},
        createRadialGradient(){return {addColorStop(){}};},putImageData(pixels){calls.preparedAlpha=[pixels.data[3],pixels.data[7]];},
        getImageData(x,y,w,h){calls.reads++;return {data:w===ART.width
            ?new Uint8ClampedArray([249,221,213,252,249,221,213,239]):[249,221,213,255]};}});
    const ctx=context(),canvas={width:0,height:0,getContext:()=>ctx},status={},still={},dolls={},classes=new Set();
    const layer={clientWidth:724,clientHeight:543,dataset:{},style:{setProperty(){}},
        classList:{add:value=>classes.add(value),remove:value=>classes.delete(value)},
        querySelector:s=>s==='.singing-character'?canvas:s==='[role="status"]'?status:s==='.singing-still'?still:s==='.singing-dolls'?dolls:null};
    class Image {constructor(){calls.images++;this.naturalWidth=ART.width;this.naturalHeight=ART.height;}
        set src(value){this.url=value;queueMicrotask(()=>this.onload());}}
    const box={window:{devicePixelRatio:1,addEventListener:(name,fn)=>listeners.add(fn),removeEventListener:(name,fn)=>listeners.delete(fn)},
        document:{createElement:()=>({width:0,height:0,getContext:()=>context()})},Image,console,
        getComputedStyle:()=>({width:'724px',height:'543px'})};
    vm.createContext(box);vm.runInContext(fs.readFileSync('frontend/static/singing-scene.js','utf8'),box);
    const scene=new box.window.SingingScene(layer,{preview,reducedMotion,breathing});
    return {scene,draws,transforms,calls,listeners,canvas,layer,still,status,box,classes,dolls};
}

test('singing mesh is bounded to 384 triangles and retains shared vertex topology',()=>{
    const mesh=buildMesh();assert.equal(mesh.triangles.length,384);assert.equal(mesh.vertices.length,221);
    assert.ok(mesh.triangles.every(t=>t.length===3&&t.every(i=>i>=0&&i<mesh.vertices.length)));
    assert.ok(mesh.vertices.every(v=>v.weights.head>=0&&v.weights.mic>=0&&v.weights.head+v.weights.mic<=1.000001));
});

test('seat remains fixed while neck, torso and elbow inherit the same seated parent',()=>{
    const anchors=[[710,1370],[900,1440],SEAT.pivot],upper=[C.neck,C.elbow,[650,1000]];
    for(let i=0;i<400;i++) {
        const pose={...timelinePose({position:i*.7,bpm:160,motionStrength:1,playing:true}),breath:Math.sin(i*.1)};
        for(const point of anchors)assert.ok(distance(deformPoint(point,pose),point)<1e-9,JSON.stringify(point));
        for(const point of upper)assert.ok(distance(deformPoint(point,pose),applyBodyPose(point,pose))<1e-9);
    }
});

test('microphone and gripping hand form one rigid group around the forearm elbow',()=>{
    const points=[[458,509],[365,609],[300,745],[400,700]];
    const pose={head:LIMITS.head,mic:-LIMITS.mic,body:LIMITS.body,breath:1},actual=points.map(p=>deformPoint(p,pose));
    for(let i=0;i<points.length;i++) {
        const weights=skinWeights(points[i]);assert.equal(weights.mic,1);assert.equal(weights.head,0);
        assert.ok(distance(actual[i],applyBodyPose(rotate(points[i],C.elbow,pose.mic),pose,points[i]))<1e-9);
        for(let j=i+1;j<points.length;j++)assert.ok(Math.abs(distance(actual[i],actual[j])-distance(points[i],points[j]))<1e-9);
    }
});

test('eyes and mouth share the head joint transform without independent image scaling',()=>{
    const points=[...C.eyes.map(([x,y,w,h])=>[x+w/2,y+h/2]),C.mouth];
    for(const point of points) {
        assert.equal(skinWeights(point).head,1);assert.equal(skinWeights(point).mic,0);
        const pose={head:LIMITS.head,mic:LIMITS.mic,body:-LIMITS.body,breath:-1};
        assert.ok(distance(deformPoint(point,pose),applyBodyPose(rotate(point,C.neck,pose.head),pose,point))<1e-9);
    }
});

test('maximal joint bends preserve triangle orientation and bounded local deformation',()=>{
    const mesh=buildMesh();
    for(const head of [-LIMITS.head,LIMITS.head])for(const mic of [-LIMITS.mic,LIMITS.mic])
    for(const body of [-LIMITS.body,LIMITS.body])for(const breath of [-1,1]) {
        const target=mesh.vertices.map(v=>deformPoint(v.point,{head,mic,body,breath},C,v.weights));
        for(const tri of mesh.triangles) {
            const source=tri.map(i=>mesh.vertices[i].point),dest=tri.map(i=>target[i]),ratio=area(...dest)/area(...source);
            assert.ok(ratio>.78&&ratio<1.22,`triangle must neither flip nor stretch wildly (${ratio})`);
        }
    }
});

test('face overlays follow the actual triangle mesh across tilted eye regions',()=>{
    const mesh=buildMesh(),landmarks=[...C.eyes.flatMap(([x,y,w,h])=>
        [[x+w*.2,y+h*.25],[x+w*.5,y+h*.5],[x+w*.8,y+h*.75]]),C.mouth];
    for(const pose of [{head:LIMITS.head,mic:-LIMITS.mic,body:LIMITS.body,breath:1},
        {head:-LIMITS.head,mic:LIMITS.mic,body:-LIMITS.body,breath:-1}])for(const p of landmarks){
        const tri=mesh.triangles.find(ids=>{
            const [a,b,c]=ids.map(i=>mesh.vertices[i].point),sum=area(a,b,c);
            return [area(p,b,c)/sum,area(a,p,c)/sum,area(a,b,p)/sum].every(w=>w>=-1e-8);
        });
        assert.ok(tri,'face landmark must lie inside mesh');
        const source=tri.map(i=>mesh.vertices[i].point),sum=area(...source),
            weights=[area(p,source[1],source[2])/sum,area(source[0],p,source[2])/sum,area(source[0],source[1],p)/sum],
            dest=tri.map(i=>deformPoint(mesh.vertices[i].point,pose,C,mesh.vertices[i].weights)),
            mapped=[0,1].map(axis=>dest.reduce((n,v,i)=>n+v[axis]*weights[i],0)),
            overlay=applyBodyPose(rotate(p,C.neck,pose.head),pose,p);
        assert.ok(distance(mapped,overlay)<1e-8,'face must not slide over the deformed head texture');
    }
});

test('choreography is deterministic on audio position and respects motion controls',()=>{
    const context={position:32.5,bpm:76,motionStrength:1,playing:true};
    assert.deepEqual(timelinePose(context),timelinePose({...context,wallTime:999999}));
    for(const disabled of [{playing:false},{reducedMotion:true},{hidden:true},{motionStrength:0}])
        assert.deepEqual(timelinePose({...context,...disabled}),{head:0,mic:0,body:0});
    for(const position of [0,.2,25,400,NaN,Infinity]) {
        const pose=timelinePose({...context,position,motionStrength:99});
        assert.ok(Number.isFinite(pose.head)&&Math.abs(pose.head)<=LIMITS.head);
        assert.ok(Number.isFinite(pose.mic)&&Math.abs(pose.mic)<=LIMITS.mic);
        assert.ok(Number.isFinite(pose.body)&&Math.abs(pose.body)<=LIMITS.body);
    }
});

test('paired eyelids and real playing state are the only mouth gate',()=>{
    const fallback={eyeOpen:.8,smile:.3,blush:.2};
    const face=pairedFace({eyeOpenL:.1,eyeOpenR:.8,mouthOpen:1},fallback,true,.7,.4);
    assert.equal(face.eyeOpenL,.1);assert.equal(face.eyeOpenR,.1);assert.equal(face.mouthOpen,.7);
    for(const playing of [false,undefined,0])assert.equal(pairedFace(null,fallback,playing,1,1).mouthOpen,0);
    const invalid=pairedFace({eyeOpenL:Infinity,eyeOpenR:NaN},fallback,true,NaN,Infinity);
    assert.equal(invalid.mouthOpen,0);assert.equal(invalid.mouthRound,0);assert.equal(invalid.eyeOpenL,invalid.eyeOpenR);
});

test('artwork is loaded once and all pixel reads happen during preparation',async()=>{
    const {scene,calls,still,classes,dolls}=fixture();scene.setActive(true,{companions:false});assert.equal(await scene.loading,true);
    assert.equal(calls.images,1);assert.equal(calls.reads,4);assert.equal(still.hidden,true);
    assert.deepEqual(calls.preparedAlpha,[255,239],'nearly opaque skin stabilizes without removing feathered transparency');
    assert.ok(classes.has('rig-ready'));assert.equal(dolls.hidden,true);
    const reads=calls.reads;scene.setActive(false);scene.setActive(true);await scene.loading;
    for(let i=0;i<10;i++)scene.render({mouthOpen:.6},1/30,false,{playing:true,position:i*.1});
    assert.equal(calls.images,1);assert.equal(calls.reads,reads);assert.equal(dolls.hidden,false);
});

test('output sizing remains sharp and never uses transformed scene dimensions',()=>{
    const {scene,canvas}=fixture();scene.resize();scene.resize();
    assert.equal(canvas.width,1440);assert.equal(canvas.height,1080);
    const {scene:preview,canvas:small}=fixture({preview:true});preview.resize();assert.equal(small.width,724);assert.equal(small.height,543);
});

test('pause freezes existing joints, closes lips, and hidden rendering makes no progress',async()=>{
    const {scene,draws}=fixture();scene.setActive(true);await scene.loading;
    const lips=[];scene.drawMouth=open=>lips.push(open);
    scene.render({mouthOpen:.8},1/30,false,{playing:true,position:15,bpm:80,motionStrength:1});
    const pose=joints(scene.pose),breath=scene.pose.breath;
    scene.render({mouthOpen:.9},1/30,false,{playing:false,position:15,bpm:80,motionStrength:1});
    assert.deepEqual(joints(scene.pose),pose);assert.notEqual(scene.pose.breath,breath);assert.deepEqual(lips,[.8,0]);
    const count=draws.length,age=scene.faceAge;
    scene.render({mouthOpen:1},1/30,true,{playing:true,position:99});
    assert.equal(draws.length,count);assert.equal(scene.faceAge,age);assert.deepEqual(joints(scene.pose),pose);
});

test('reduced motion keeps seated geometry static but preserves actual singing lips',async()=>{
    const {scene}=fixture({reducedMotion:true});scene.setActive(true);await scene.loading;
    const lips=[];scene.drawMouth=open=>lips.push(open);scene.setAction({name:'wave',mouthOpen:1});
    scene.render({mouthOpen:.65},1/30,false,{playing:true,position:18,motionStrength:1});
    assert.deepEqual({...scene.pose},{head:0,mic:0,body:0,breath:0});assert.deepEqual(lips,[.65]);
});

test('intro voice can open the jaw without starting the recording choreography',async()=>{
    const {scene}=fixture();scene.setActive(true);await scene.loading;
    const lips=[];scene.drawMouth=open=>lips.push(open);
    scene.render({mouthOpen:.75},1/30,false,{playing:false,voicePlaying:true,position:0});
    assert.deepEqual(lips,[.75]);assert.deepEqual(joints(scene.pose),{head:0,mic:0,body:0});
    scene.render({mouthOpen:.75},1/30,false,{playing:true,voicePlaying:false,position:2});
    assert.deepEqual(lips,[.75,0]);
});

test('fallback blinking closes both eyes smoothly and unknown actions cannot alter joints',()=>{
    const {scene}=fixture();scene.setSinging(true);scene.setExpression('blink');
    let minimum=1;
    for(let i=0;i<24;i++) {const f=scene.sampleFace(1/60,null,false,1,1);minimum=Math.min(minimum,f.eyeOpen);
        assert.equal(f.eyeOpenL,f.eyeOpenR);assert.equal(f.mouthOpen,0);}
    assert.ok(minimum<.01);const reopened=scene.sampleFace(1/60,null,false,0,0);
    assert.ok(reopened.eyeOpen>.6);assert.equal(reopened.eyeOpen,scene.current.eyeOpen);
    const before={...scene.pose};scene.setAction({name:'wild_dance',head:100,mic:100,mouthOpen:1});
    assert.deepEqual({...scene.pose},before);scene.setExpression('unsupported');assert.equal(scene.expression,'neutral');
});

test('idle breathing continues while song is paused without changing its phase or joints',async()=>{
    const {scene,draws}=fixture();scene.setActive(true);await scene.loading;
    const before=draws.length;
    for(let i=0;i<12;i++)scene.render({},.1,false,{playing:false,position:18});
    assert.ok(scene.pose.breath>.99);assert.deepEqual(joints(scene.pose),{head:0,mic:0,body:0});
    assert.ok(draws.slice(before).filter(call=>call[0]===scene.body).length>=384,
        'idle breathing must render the mesh rather than bypassing it with the neutral still');
    const clock=scene.breathClock;
    scene.render({},.1,true,{playing:false,position:18});assert.equal(scene.breathClock,clock);
    scene.setBreathing(false);scene.render({},.1,false,{playing:false,position:18});
    assert.equal(scene.pose.breath,0);assert.equal(scene.breathClock,clock);
    scene.setBreathing(true);scene.render({},.1,false,{playing:false,position:18});assert.ok(scene.pose.breath>.9);
});

test('breathing lifts the connected upper body visibly while its hidden seat remains anchored',()=>{
    const points=[[650,710],[760,640],[500,700],[800,700],[600,735]],
        contacts=[C.neck,C.elbow,[458,509],[365,609],[300,745],[650,1000]];
    for(const point of points)assert.ok(distance(deformPoint(point,{breath:1}),point)<=11.86);
    for(const point of contacts)assert.ok(distance(deformPoint(point,{breath:1}),[point[0],point[1]-10])<1e-9);
    for(const point of [SEAT.pivot,[710,1370]])assert.ok(distance(deformPoint(point,{breath:1}),point)<1e-9);
    const mesh=buildMesh();for(const breath of [-1,1])for(const tri of mesh.triangles) {
        const source=tri.map(i=>mesh.vertices[i].point),target=source.map(p=>deformPoint(p,{breath}));
        assert.ok(area(...target)/area(...source)>.9,'breathing may not fold or distort triangles');
    }
});

test('breathing can be disabled in scene options while blinking and actual jaw remain active',async()=>{
    const {scene}=fixture({breathing:false});scene.setActive(true);await scene.loading;
    const lips=[];scene.drawMouth=open=>lips.push(open);scene.setExpression('blink');
    for(let i=0;i<30;i++)scene.render({mouthOpen:.6},1/30,false,{playing:true,position:20,motionStrength:0});
    assert.equal(scene.pose.breath,0);assert.equal(scene.breathClock,0);assert.ok(lips.every(v=>v===.6));
    scene.setActive(true,{breathing_enabled:true});scene.render({},.1,false,{playing:false,position:20});
    assert.ok(scene.pose.breath>0);
});

test('disposal removes owned resize listener and prevents late load activation',async()=>{
    const {scene,listeners}=fixture();scene.setActive(true);const pending=scene.loading;scene.dispose();
    assert.equal(await pending,false);assert.equal(scene.ready,false);assert.equal(listeners.size,0);
    scene.setActive(true);assert.equal(scene.active,false);
});

test('ending eases the seated pose to rest but pausing retains the exact musical pose',async()=>{
    const {scene}=fixture();scene.setActive(true);await scene.loading;scene.setSinging(true);
    scene.render({},.1,false,{playing:true,position:15,bpm:80,motionStrength:1});
    const playing={...scene.pose};
    scene.render({},.1,false,{playing:false,position:15});
    assert.deepEqual(joints(scene.pose),joints(playing));
    scene.setSinging(false);scene.render({},.1,false,{playing:false,position:15});
    assert.ok(Math.abs(scene.pose.body)<Math.abs(playing.body));
    assert.ok(Math.abs(scene.pose.body)>0,'return to rest must not snap');
    for(let i=0;i<40;i++)scene.render({},.1,false,{playing:false,position:15});
    assert.deepEqual(joints(scene.pose),{head:0,mic:0,body:0});
    scene.setSinging(true);scene.render({},.1,false,{playing:true,position:15,bpm:80});
    scene.reducedMotion=true;scene.render({},.1,false,{playing:false,position:15});
    assert.deepEqual({...scene.pose},{head:0,mic:0,body:0,breath:0});
});

test('adjacent texture patches overlap their shared edge so idle breathing cannot reveal a mesh grid',()=>{
    const crops=[],ctx={save(){},restore(){},beginPath(){},moveTo(){},lineTo(){},closePath(){},clip(){},transform(){},drawImage(...args){crops.push(args);}};
    const image={width:ART.width,height:ART.height};
    const left=[[150,300],[275,300],[150,382]],right=[[275,300],[360,300],[275,382]];
    drawTriangle(ctx,image,left,left);drawTriangle(ctx,image,right,right);
    assert.equal(crops.length,2);
    const first=crops[0],second=crops[1];
    assert.ok(first[1]+first[3]>second[1], 'texture crops must share real texels across the common vertical edge');
    assert.ok(first[1]<150&&first[2]<300&&second[1]<275);
    assert.ok(first[1]+first[3]<=ART.width&&first[2]+first[4]<=ART.height);
});
