const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {test}=require('node:test');
function fixture(options={}) {
    const calls={draws:0,clears:0},ctx={drawImage(){calls.draws++;},clearRect(){calls.clears++;},setTransform(){},save(){},restore(){}};
    const canvas={getContext:()=>ctx},layer={style:{},dataset:{},querySelector:()=>canvas};
    const box={window:{devicePixelRatio:1},getComputedStyle:()=>({width:'714px',height:'535.5px'}),console};
    vm.createContext(box);vm.runInContext(fs.readFileSync('frontend/static/car-drive.js','utf8'),box);
    const drive=new box.window.CarDrive(layer,options);drive.loading=Promise.resolve();drive.ready=true;
    drive.texture={width:1164};drive.mask={};drive.setActive(true,{});drive.resize();
    return {Drive:box.window.CarDrive,drive,calls,canvas,layer};
}
test('loop covers every window across the wrap without gaps or an unbounded tile count',()=>{
    const {Drive}=fixture(),width=1164,period=width/Drive.speed;
    for(const time of [0,.1,period-.001,period,period+.001,86400]) {
        const tiles=Drive.tilePositions(time,width);
        assert.ok(tiles.length<=3);assert.ok(tiles[0]<=0);assert.ok(tiles.at(-1)+width>=1448);
        for(let i=1;i<tiles.length;i++)assert.ok(Math.abs(tiles[i]-tiles[i-1]-width)<1e-9);
    }
    assert.deepEqual(Array.from(Drive.tilePositions(0,width)),Array.from(Drive.tilePositions(period,width)));
});
test('driving paints at 20 fps or less, pauses when hidden and leaves original artwork when switched off',()=>{
    const {drive,calls,canvas,layer}=fixture();assert.equal(canvas.width,1440);assert.equal(canvas.height,1080);
    for(let i=0;i<60;i++)drive.render(1/60);
    assert.ok(calls.clears>=18&&calls.clears<=21);assert.ok(calls.draws<=84);
    const before=calls.draws,time=drive.time;drive.render(.1,true);
    assert.equal(calls.draws,before);assert.equal(drive.time,time);
    assert.ok(layer.style.transform.includes('scale(1.004)'));
    drive.setActive(true,{car_motion:false});drive.render(.1);
    assert.equal(canvas.hidden,true);assert.equal(layer.style.transform,'');assert.equal(calls.draws,before);
    drive.setActive(false);drive.render(.1);assert.equal(calls.draws,before);
});
test('reduced motion uses a still exterior and camera vibration can be disabled independently',()=>{
    const {drive,calls,layer}=fixture({reducedMotion:true});
    for(let i=0;i<90;i++)drive.render(1/30);
    assert.equal(calls.clears,1);assert.equal(drive.time,0);assert.equal(layer.style.transform,'');
    const normal=fixture();normal.drive.setActive(true,{car_vibration:false});normal.drive.render(.1);
    assert.ok(normal.calls.draws>0);assert.equal(normal.layer.style.transform,'');
});
test('camera shake stays subpixel and applies one transform to the entire shot',()=>{
    const {Drive}=fixture();
    for(let i=0;i<1000;i++) {
        const value=Drive.camera(i/30),numbers=value.match(/-?[\d.]+/g).map(Number);
        assert.ok(Math.abs(numbers[0])<.03);assert.ok(Math.abs(numbers[1])<.06);
        assert.ok(Math.abs(numbers[2])<=.012);assert.equal(numbers[3],1.004);
    }
});
