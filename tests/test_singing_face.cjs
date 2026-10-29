const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {test}=require('node:test');
const {SingingScene,DEFAULT_CALIBRATION:C,EXPRESSIONS,eyeBasis,expressionTarget,upperLidShape}=require('../frontend/static/singing-scene.js');
const close=(a,b)=>assert.ok(Math.abs(a-b)<1e-9,`${a} ≠ ${b}`);
const transform=(m,[x,y])=>[m[0]*x+m[2]*y,m[1]*x+m[3]*y];
function faceScene(){
    const scene=Object.create(SingingScene.prototype);
    Object.assign(scene,{current:{...EXPRESSIONS.neutral},expression:'neutral',faceAge:0,blinkWait:3.4,blinkAge:-1});
    return scene;
}
function settle(scene,snapshot,seconds=1.5){let face;for(let i=0;i<seconds*60;i++)face=scene.sampleFace(1/60,snapshot,false,1,1);return face;}
function controller(){
    const box={window:{},console};vm.createContext(box);
    vm.runInContext(fs.readFileSync('frontend/static/vendor/expression-engine.bundle.js','utf8'),box);
    return new box.window.CharacterExpressionController({seed:37,intensity:.55});
}

test('tilted blink preserves eye tangent width and closes along each local normal',()=>{
    assert.notEqual(C.eyeAngles[0],C.eyeAngles[1],'artwork perspective needs separate calibration');
    for(const angle of C.eyeAngles){
        const tangent=[Math.cos(angle),Math.sin(angle)],normal=[-Math.sin(angle),Math.cos(angle)];
        for(const openness of [0,.1,.5,1]){
            const basis=eyeBasis(angle,openness),t=transform(basis,tangent),n=transform(basis,normal);
            close(t[0],tangent[0]);close(t[1],tangent[1]);close(n[0],normal[0]*openness);close(n[1],normal[1]*openness);
        }
        eyeBasis(angle,1).forEach((value,index)=>close(value,[1,0,0,1][index]));
    }
});

test('semantic shared snapshots produce recognizable different calibrated emotions at saved strength',()=>{
    const faces={};
    for(const name of Object.keys(EXPRESSIONS)){
        const scene=faceScene();faces[name]=settle(scene,{expression:name,intensity:.55,enabled:true,
            eyeOpenL:1,eyeOpenR:1,smile:.22,blush:.04,blink:0});
        assert.equal(faces[name].eyeOpenL,faces[name].eyeOpenR);
        assert.equal(faces[name].mouthOpen,0,'emotion must never invent voiced jaw movement');
    }
    assert.ok(faces.happy.eyeOpen<.74);assert.ok(faces.happy.smile>.62);
    assert.ok(faces.shy.blush>.55);assert.ok(faces.sad.smile<-.35);
    assert.ok(faces.angry.browTilt>.5&&faces.sad.browTilt<-.4);
    assert.ok(faces.surprised.browLift>.5&&faces.surprised.purse>.45);
    assert.ok(faces.thinking.purse>.3&&faces.thinking.smile<faces.neutral.smile);
    assert.equal(new Set(Object.values(faces).map(f=>JSON.stringify(f))).size,Object.keys(EXPRESSIONS).length);
});

test('shared SDK blink drives one complete paired cycle instead of being lost in semantic mapping',()=>{
    const c=controller(),scene=faceScene();c.setExpression('happy',{hold:true});c.blink();
    let minimum=1,last;
    for(let i=0;i<45;i++){
        const snapshot=c.sample(1/60);last=scene.sampleFace(1/60,snapshot,false,1,1);
        minimum=Math.min(minimum,last.eyeOpen);assert.equal(last.eyeOpenL,last.eyeOpenR);
    }
    assert.equal(minimum,0);assert.ok(last.eyeOpen>.7);
    assert.equal(scene.blinkAge,-1,'the shared clock must not cause a second local blink');
});

test('controller preserves disabled and zero-intensity module expression settings in the singer',()=>{
    const c=controller(),scene=faceScene();
    c.setModule('singing',{expression:'happy',expression_strength:.55,expression_enabled:true});
    let last;for(let i=0;i<90;i++)last=scene.sampleFace(1/60,c.sample(1/60),false,0,0);
    assert.ok(last.smile>.62);
    c.setModule('singing',{expression:'angry',expression_strength:1,expression_enabled:false});
    let snapshot;
    for(let i=0;i<90;i++){snapshot=c.sample(1/60);last=scene.sampleFace(1/60,snapshot,false,0,0);}
    assert.equal(snapshot.enabled,false);assert.equal(snapshot.intensity,0);
    assert.ok(Math.abs(last.smile-EXPRESSIONS.neutral.smile)<.001);assert.ok(Math.abs(last.browTilt)<.001);
    assert.deepEqual(expressionTarget('happy',0),EXPRESSIONS.neutral);
});

test('semantic expression changes blend from the existing face without a discontinuous frame',()=>{
    const scene=faceScene(),before=settle(scene,{expression:'happy',intensity:1,blink:0});
    const instant=scene.sampleFace(0,{expression:'sad',intensity:1,blink:0},false,0,0);
    assert.deepEqual(instant,before);
    const next=scene.sampleFace(1/60,{expression:'sad',intensity:1,blink:0},false,0,0);
    assert.ok(next.smile<before.smile&&Math.abs(next.smile-before.smile)<.21);
});

test('mouth opening follows actual audio, not the surprised or singing emotion',()=>{
    const scene=faceScene();
    for(const emotion of ['surprised','singing','angry']){
        const snapshot={emotion,intensity:1,blink:0,mouthOpen:1};
        assert.equal(scene.sampleFace(1/60,snapshot,false,.9,.5).mouthOpen,0);
        const face=scene.sampleFace(1/60,snapshot,true,.63,.24);
        assert.equal(face.mouthOpen,.63);assert.equal(face.mouthRound,.24);
        assert.equal(scene.sampleFace(1/60,snapshot,true,0,0).mouthOpen,0);
    }
});

test('manual blink still closes both eyes when an older external adapter has no blink metadata',()=>{
    const scene=faceScene();scene.blinkAge=0;
    let minimum=1;
    for(let i=0;i<24;i++)minimum=Math.min(minimum,scene.sampleFace(1/60,{eyeOpenL:.8,eyeOpenR:.9},false,0,0).eyeOpen);
    assert.equal(minimum,0);
});

test('angry and sad upper lids occlude opposite corners without changing eye width',()=>{
    for(const [index,eye] of C.eyes.entries()){
        const [,,width,height]=eye,angry=upperLidShape(expressionTarget('angry',.55),index,width,height),
            sad=upperLidShape(expressionTarget('sad',.55),index,width,height),
            neutral=upperLidShape(expressionTarget('angry',0),index,width,height);
        assert.ok(angry.amount>.8&&sad.amount>.6,'default strength must visibly shape the lid');
        assert.equal(neutral.amount,0,'disabled/zero strength must leave the original eye unchanged');
        const inner=index?'left':'right',outer=index?'right':'left';
        assert.ok(angry[inner][1]>angry[outer][1]+12,'angry inner eyelid lowers toward the nose');
        assert.ok(sad[outer][1]>sad[inner][1]+11,'sad outer eyelid droops while inner lid rises');
        close(angry.left[0],sad.left[0]);close(angry.right[0],sad.right[0]);
    }
});
