const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const {test}=require('node:test');

function fixture() {
    const pixels=new Uint8ClampedArray(8*8*4);
    for(let y=2;y<6;y++) for(let x=2;x<6;x++) pixels[(y*8+x)*4+3]=255;
    pixels[3]=255;
    let cached;
    const sandbox={window:{},document:{createElement(){return {getContext(){return {
        drawImage(){}, getImageData(){return {data:pixels};},putImageData(value){cached=value;}
    };}};}}, Uint32Array,console};
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync('frontend/static/cutout-rig.js','utf8')+'\nthis.Rig=CutoutRig;',sandbox);
    return {Rig:sandbox.Rig,pixels,cached:()=>cached};
}

test('atlas extraction keeps the character part and removes neighbouring alpha flecks',()=>{
    const {Rig,pixels,cached}=fixture();
    const rig=new Rig({width:8,height:8},{parts:{head:{cell:[0,0,1,1],height:100,pivot:[.5,.5]}}});
    assert.deepEqual(Array.from(rig.parts.head.bounds),[2,2,4,4]);
    assert.equal(cached().data[3],0,'a disconnected fleck must not float beside the head');
    assert.equal(pixels[(3*8+3)*4+3],255,'the part alpha must be preserved');
});

test('forearms attach at painted elbows and keep hands joined to wrists',()=>{
    const {Rig}=fixture();
    const rig=Object.create(Rig.prototype),calls=[];
    rig.piece=(context,name,x,y,angle,sx,sy,clip)=>calls.push({name,matrix:[...context.matrix],clip});
    const stack=[],ctx={matrix:[1,0,0,1,0,0],save(){stack.push([...this.matrix]);},restore(){this.matrix=stack.pop();},
        translate(x,y){const [a,b,c,d,e,f]=this.matrix;this.matrix=[a,b,c,d,e+a*x+c*y,f+b*x+d*y];},
        rotate(angle){const [a,b,c,d,e,f]=this.matrix,s=Math.sin(angle),co=Math.cos(angle);this.matrix=[a*co+c*s,b*co+d*s,-a*s+c*co,-b*s+d*co,e,f];}};
    rig.arm(ctx,'left',{leftShoulder:.4,leftElbow:.5,leftWrist:.2});
    assert.equal(calls.length,2);
    const forearm=calls[0],upper=calls[1];
    assert.equal(upper.name,'leftUpper');
    assert.ok(Math.abs(forearm.matrix[4]-(401-Math.cos(.4)*38-Math.sin(.4)*155))<1e-8);
    assert.ok(Math.abs(forearm.matrix[5]-(355-Math.sin(.4)*38+Math.cos(.4)*155))<1e-8);
    assert.ok(Math.abs(forearm.matrix[0]-Math.cos(.9))<1e-8);
    assert.equal(forearm.clip,undefined,'do not cut the painted wrist into disjoint pieces');
});
