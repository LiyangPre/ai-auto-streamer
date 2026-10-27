const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');

function classes(reduced = false) {
    const sandbox = {window: {addEventListener() {}, location:{search:''}, matchMedia() { return {matches: reduced}; }},
        getComputedStyle:canvas=>({width:canvas.clientWidth+'px',height:canvas.clientHeight+'px'}),
        console, URLSearchParams, performance};
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync('frontend/static/character-motion.js', 'utf8') +
        fs.readFileSync('frontend/static/character-face.js', 'utf8') +
        fs.readFileSync('frontend/static/app.js', 'utf8') +
        '\nthis.Motion = CharacterMotion; this.Sprite = SpriteRenderer;', sandbox);
    return sandbox;
}

test('all one-shot gestures settle back to idle without a stuck lean', () => {
    const {Motion} = classes();
    for (const name of Object.keys(Motion.durations).filter(name => name !== 'idle')) {
        const gesture = new Motion(), idle = new Motion();
        gesture.setAction({name});
        let actual, expected;
        for (let frame = 0; frame < 600; frame++) {
            actual = gesture.sample(1 / 30);
            expected = idle.sample(1 / 30);
            for (const value of Object.values(actual)) assert.ok(Number.isFinite(value), name);
            assert.ok(Math.abs(actual.roll) < 0.05, `${name} roll must stay gentle`);
            assert.ok(Math.abs(actual.x) < 0.02, `${name} must not shake wildly`);
        }
        for (const key of ['roll', 'bow', 'armLift', 'armWave', 'mouth', 'flutter']) {
            assert.ok(Math.abs(actual[key] - expected[key]) < 0.0001, `${name} must release ${key}`);
        }
    }
});

test('choreography timing is stable at 30 and 60 fps', () => {
    const {Motion} = classes();
    const slow = new Motion(), fast = new Motion();
    slow.setAction({name: 'wave'}); fast.setAction({name: 'wave'});
    for (let i = 0; i < 30; i++) slow.sample(1 / 30);
    for (let i = 0; i < 60; i++) fast.sample(1 / 60);
    assert.ok(Math.abs(slow.current.armLift - fast.current.armLift) < 0.01);
    assert.ok(Math.abs(slow.current.roll - fast.current.roll) < 0.002);
});

test('reduced motion preserves lip sync without body or wing movement', () => {
    const {Motion} = classes();
    const motion = new Motion(true);
    motion.setAction({name: 'surprise'});
    for (let frame = 0; frame < 30; frame++) motion.sample(1 / 30, 0.7, true);
    assert.ok(motion.current.mouth > 0.6);
    for (const key of ['x', 'y', 'roll', 'bow', 'pulse', 'flutter', 'armLift']) assert.equal(motion.current[key], 0);
});

test('zero movement strength keeps speech lip sync and suppresses gestures', () => {
    const {Motion} = classes();
    const motion = new Motion();
    motion.setAction({name: 'wave', intensity: 0});
    for (let frame = 0; frame < 30; frame++) motion.sample(1 / 30, 0.7, true);
    assert.ok(motion.current.mouth > 0.6);
    for (const key of ['x', 'y', 'roll', 'bow', 'pulse', 'flutter', 'armLift', 'armWave']) assert.equal(motion.current[key], 0);
});

test('interrupted cute poses preserve position and velocity without a snap', () => {
    const {Motion}=classes();
    const motion=new Motion();
    motion.setAction({name:'wave',loop:true});
    for(let i=0;i<45;i++) motion.sample(1/30);
    const before={...motion.current}, velocity={...motion.velocity};
    motion.setAction({name:'heart',loop:true});
    assert.deepEqual({...motion.current},before);
    assert.deepEqual({...motion.velocity},velocity);
    motion.sample(1/30);
    for(const key of ['leftShoulder','leftElbow','rightShoulder','rightElbow','headRoll']) {
        assert.ok(Math.abs(motion.current[key]-before[key])<.18,`${key} must blend from its current position`);
    }
    for(const name of ['shy','peek','cheer','double_wave','heart','curtsy','stretch','present']) {
        motion.setAction({name});
        for(let i=0;i<12;i++) {
            const pose=motion.sample(i===3?.15:1/30);
            assert.ok(Object.values(pose).every(Number.isFinite));
            for(const key of ['leftShoulder','leftElbow','rightShoulder','rightElbow']) assert.ok(Math.abs(pose[key])<4);
        }
    }
});

test('all poses and rapid interruptions respect the cutout limits and planted feet', () => {
    const {Motion}=classes();
    const motion=new Motion();
    for(const name of [...Object.keys(Motion.durations),...Object.keys(Motion.aliases)]) {
        motion.setAction({name,intensity:100,loop:true});
        for(let frame=0;frame<90;frame++) {
            const pose=motion.sample(frame%13===0?.2:1/30);
            for(const [joint,[min,max]] of Object.entries(Motion.limits))
                assert.ok(pose[joint]>=min&&pose[joint]<=max,`${name}: ${joint}`);
            // Even the outward greeting remains below waist level, outside the torso.
            const elbowX=401-Math.sin(pose.leftShoulder)*155-Math.cos(pose.leftShoulder)*38;
            const wristY=355+Math.cos(pose.leftShoulder)*155+Math.cos(pose.leftShoulder+pose.leftElbow)*100;
            assert.ok(elbowX<390&&wristY>580,'arms must not cross through the torso or face');
        }
    }
});

test('silent actions never flap the mouth and speech has no extra body-spring latency',()=>{
    const {Motion}=classes();const motion=new Motion();
    for(const name of ['talk','laugh','shy','present']) {
        motion.setAction({name,loop:true});
        assert.equal(motion.sample(1/30,.8,false).mouth,0);
        assert.equal(motion.sample(1/30,.7,true).mouth,.7);
        assert.equal(motion.sample(1/30,0,true).mouth,0);
    }
});

test('single-artwork character does not fade to transparent for missing expressions', () => {
    const {Sprite} = classes();
    const sprite = new Sprite({getContext() { return {}; }}, {sprite_mouth: {x: .5, y: .157}});
    sprite.sprites.neutral = {width: 1024, height: 1536};
    sprite.setAction({name: 'wave', sprite: 'happy', sprite_motion: 'sway'});
    assert.equal(sprite.targetSprite, 'neutral');
    assert.equal(sprite.crossfade, 1);
});

test('resizing uses untransformed layout dimensions so scene scale cannot compound',()=>{
    const {Sprite}=classes();
    const canvas={clientWidth:640,clientHeight:360,getContext(){return {setTransform(){}};},
        getBoundingClientRect(){throw new Error('transformed geometry must not feed back into render sizing');}};
    const sprite=new Sprite(canvas,{});sprite.resize();sprite.resize();
    assert.equal(sprite.viewW,640);assert.equal(sprite.viewH,360);
    assert.equal(canvas.width,1920);assert.equal(canvas.height,1080);
});
