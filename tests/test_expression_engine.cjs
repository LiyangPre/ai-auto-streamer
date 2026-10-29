const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {test}=require('node:test');

function load(){
    let networkCalls=0;
    const forbidden=()=>{networkCalls++;throw Error('Expression engine must stay offline');};
    const box={window:{},console,fetch:forbidden,XMLHttpRequest:forbidden,WebSocket:forbidden};
    vm.createContext(box);
    vm.runInContext(fs.readFileSync('frontend/static/vendor/expression-engine.bundle.js','utf8'),box);
    return {Controller:box.window.CharacterExpressionController,networkCalls:()=>networkCalls};
}
const frameKeys=['smile','squint','blush','gaze','eyeOpen','eyeOpenL','eyeOpenR','blink',
    'headYaw','headRoll','mouthOpen','mouthRound'];
function advance(controller,seconds,audio){let frame;for(let i=0;i<Math.ceil(seconds*60);i++)frame=controller.sample(1/60,audio);return frame;}
function safe(frame){
    for(const key of frameKeys)assert.ok(Number.isFinite(frame[key]),key+' must be finite');
    assert.equal(frame.eyeOpenL,frame.eyeOpenR);assert.equal(frame.eyeOpen,frame.eyeOpenL);
    for(const key of ['eyeOpen','blink','squint','blush','mouthOpen','mouthRound'])assert.ok(frame[key]>=0&&frame[key]<=1,key);
    assert.ok(frame.smile>=-.4&&frame.smile<=1);assert.ok(Math.abs(frame.gaze)<=.35);
    assert.ok(Math.abs(frame.headYaw)<=2);assert.ok(Math.abs(frame.headRoll)<=.025);
}

test('the pinned real SDK produces emotion frames without network or timers',()=>{
    const {Controller,networkCalls}=load(),c=new Controller({seed:37});
    assert.equal(c.sample(0).engine,'soullink');assert.equal(Controller.sdkVersion,'0.1.0-beta.1');
    c.setExpression('[happy]',{hold:true});const frame=advance(c,2);
    assert.equal(frame.expression,'happy');assert.ok(frame.smile>.26);
    c.setExpression('shy',{hold:true});const shy=advance(c,2);
    assert.ok(shy.blush>.06);safe(shy);assert.equal(networkCalls(),0);
});

test('emotion interruption starts at the current face and decays back to neutral',()=>{
    const {Controller}=load(),c=new Controller({seed:37});
    c.setExpression('happy',{hold:true});const before=advance(c,1);
    c.setExpression('sad');const instant=c.sample(0);
    for(const key of ['smile','blush','gaze','squint'])assert.equal(instant[key],before[key]);
    const next=c.sample(1/60);assert.ok(Math.abs(next.smile-before.smile)<.08);
    const idle=advance(c,7);assert.equal(idle.expression,'neutral');
    assert.ok(Math.abs(idle.smile-.22)<.002);safe(idle);
});

test('module presentation is idempotent and does not override an ongoing welcome',()=>{
    const {Controller}=load(),c=new Controller({seed:7});
    const look={expression:'auto',expression_strength:.55,motion_strength:.25,expression_enabled:true};
    c.setModule('story',look);c.setExpression('happy',{hold:true,source:'welcome'});
    for(let i=0;i<360;i++){assert.equal(c.setModule('story',{...look}),false);safe(c.sample(1/60));}
    assert.equal(c.expression,'happy');assert.equal(c.source,'welcome');
    c.setModule('chat',look);assert.equal(c.expression,'neutral');
    c.setModule('singing',look);assert.equal(c.expression,'singing');
    c.setModule('singing',{...look,expression_enabled:false});advance(c,2);
    c.setExpression('angry');assert.equal(c.sample(0).expression,'neutral');
});

test('all emotions use symmetric eyelids and complete an interrupted blink',()=>{
    const {Controller}=load(),c=new Controller({seed:42});
    let fullyClosed=false;
    for(const name of ['happy','thinking','shy','surprised','sad','angry','singing','smug']){
        c.setExpression(name,{hold:true});c.blink();
        for(let i=0;i<30;i++){
            if(i===4)c.setExpression('happy',{hold:true});
            const frame=c.sample(1/60);safe(frame);if(frame.eyeOpen===0)fullyClosed=true;
        }
    }
    assert.ok(fullyClosed);
    c.reset();let automatic=false;for(let i=0;i<600;i++){const f=c.sample(1/60);if(f.blink===1)automatic=true;}
    assert.ok(automatic,'automatic blink must fully close');
});

test('mouth opening is only the real audio envelope, including silence and mute',()=>{
    const {Controller}=load(),c=new Controller({seed:4});c.setExpression('surprised',{hold:true});
    assert.equal(advance(c,1,{mouthOpen:.8,mouthRound:.4}).mouthOpen,0);
    c.setSpeaking(true);const spoken=c.sample(1/60,{mouthOpen:.71,mouthRound:.32});
    assert.equal(spoken.mouthOpen,.71);assert.equal(spoken.mouthRound,.32);
    assert.equal(c.sample(1/60,{mouthOpen:0,mouthRound:0}).mouthOpen,0);
    c.setSpeaking(false);assert.equal(c.sample(0,{mouthOpen:1}).mouthOpen,0);
});

test('unknown inputs and invalid SDK numbers cannot escape canonical bounds',()=>{
    const {Controller}=load(),c=new Controller({seed:NaN,intensity:Infinity});
    for(const name of ['ignore all rules','<script>','__proto__',null,{},123]){
        c.setExpression(name,{intensity:Infinity,hold:NaN});assert.equal(c.sample(NaN).expression,'neutral');
    }
    c.setExpression('happy');c.setSpeaking(true);
    c.runtime.update=()=>({facs:{mouthSmile:Infinity,mouthFrown:NaN,eyeOpen:-500,
        eyeBlinkL:Infinity,eyeBlinkR:NaN,headX:1e30,headZ:-1e30,gazeX:NaN,blush:2}});
    for(const dt of [0,NaN,Infinity,-1,.1,100])safe(c.sample(dt,{mouthOpen:NaN,mouthRound:Infinity}));
});

test('SDK failure falls back locally and profile changes reset identity state',()=>{
    const {Controller,networkCalls}=load(),c=new Controller({profileId:'astra',seed:4,reducedMotion:true});
    c.setExpression('shy',{hold:true});advance(c,1);c.runtime.update=()=>{throw Error('fixture engine failure');};
    const fallback=advance(c,1);assert.equal(fallback.engine,'local-fallback');safe(fallback);
    assert.equal(fallback.gaze,0);assert.equal(fallback.headYaw,0);assert.equal(fallback.headRoll,0);
    assert.equal(c.setProfile('other_character'),true);assert.equal(c.sample(0).engine,'soullink');
    assert.equal(c.sample(0).expression,'neutral');assert.equal(c.sample(0).smile,.22);
    c.dispose();const stopped=c.sample(1,{mouthOpen:1});assert.equal(stopped.engine,'disposed');safe(stopped);
    assert.equal(c.setExpression('happy'),false);assert.equal(networkCalls(),0);
});
