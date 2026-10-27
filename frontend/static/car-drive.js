// Cached 2D scenery, composited through glass only. No video decoder, network
// frame loop or independent movement of hands, steering wheel and companions.
class CarDrive {
    static width=1448;
    static height=1086;
    static glassHeight=485;
    static speed=12; // Reference-art pixels / second: a leisurely side-window view.
    constructor(layer,{preview=false,reducedMotion=false}={}) {
        this.layer=layer;this.canvas=layer.querySelector('.car-windows');
        this.ctx=this.canvas.getContext('2d');this.preview=preview;this.reducedMotion=reducedMotion;
        this.active=false;this.motion=true;this.vibration=true;this.ready=false;
        this.time=0;this.elapsed=1;this.painted=false;
    }
    setActive(active,settings={}) {
        this.active=!!active;this.motion=settings.car_motion!==false;this.vibration=settings.car_vibration!==false;
        this.canvas.hidden=!this.active||!this.motion||!this.ready;
        this.elapsed=1;this.painted=false;
        if(!this.active||!this.motion||!this.vibration||this.reducedMotion)this.layer.style.transform='';
        if(this.active&&this.motion&&!this.loading)this.loading=this.load();
    }
    async load() {
        const image=name=>new Promise((resolve,reject)=>{
            const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error('Driving scenery unavailable; keeping still artwork.'));
            img.src='/static/scenes/car/'+name;
        });
        this.layer.dataset.driveState='loading';
        try {
            const [panorama,cutout]=await Promise.all([image('countryside-loop-v1.png'),image('window-cutout-v1.png')]);
            this.texture=this.makeLoop(panorama);this.mask=this.makeWindowMask(cutout);
            this.ready=true;this.layer.dataset.driveState='ready';
            this.canvas.hidden=!this.active||!this.motion;this.elapsed=1;this.resize();
        } catch(error){this.layer.dataset.driveState='unavailable';console.warn(error.message);}
    }
    makeLoop(image) {
        // Blend the last 20% into the first 20% once. The wrap has the same
        // source pixels on either side, even if painted edges do not tile exactly.
        const h=CarDrive.glassHeight,w=Math.round(image.width/image.height*h),overlap=Math.round(w*.2);
        const source=document.createElement('canvas');source.width=w;source.height=h;
        source.getContext('2d').drawImage(image,0,0,w,h);
        const loop=document.createElement('canvas');loop.width=w-overlap;loop.height=h;
        const ctx=loop.getContext('2d');ctx.drawImage(source,0,0);
        ctx.drawImage(source,w-overlap,0,overlap,h,0,0,overlap,h);
        const blend=document.createElement('canvas');blend.width=overlap;blend.height=h;
        const mix=blend.getContext('2d');mix.drawImage(source,0,0);
        mix.globalCompositeOperation='destination-in';const ramp=mix.createLinearGradient(0,0,overlap,0);
        ramp.addColorStop(0,'#fff0');ramp.addColorStop(1,'#fff');mix.fillStyle=ramp;mix.fillRect(0,0,overlap,h);
        ctx.drawImage(blend,0,0);return loop;
    }
    makeWindowMask(cutout) {
        const mask=document.createElement('canvas');mask.width=CarDrive.width;mask.height=CarDrive.glassHeight;
        const ctx=mask.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,mask.width,mask.height);
        ctx.globalCompositeOperation='destination-out';ctx.drawImage(cutout,0,0,CarDrive.width,CarDrive.height);
        // Only the alpha is used: all original interior/character colors stay
        // untouched. Keep the original mirror reflection and its frame as well.
        ctx.globalCompositeOperation='source-over';ctx.clearRect(1335,0,113,125);
        return mask;
    }
    resize() {
        const layout=getComputedStyle(this.layer),w=parseFloat(layout.width)||this.layer.clientWidth,h=parseFloat(layout.height)||this.layer.clientHeight;
        if(!w||!h)return;
        const dpr=this.preview?Math.min(window.devicePixelRatio||1,1.5):Math.min(2160/h,Math.max(window.devicePixelRatio||1,1080/h));
        this.canvas.width=Math.round(w*dpr);this.canvas.height=Math.round(h*dpr);
        this.ctx.setTransform(this.canvas.width/CarDrive.width,0,0,this.canvas.height/CarDrive.height,0,0);
        this.ctx.imageSmoothingEnabled=true;this.ctx.imageSmoothingQuality='high';this.elapsed=1;this.painted=false;
    }
    static tilePositions(time,width) {
        if(!(width>0))return [];
        const offset=((time*CarDrive.speed)%width+width)%width,positions=[];
        for(let x=-offset;x<CarDrive.width;x+=width)positions.push(x);
        return positions;
    }
    static camera(time) {
        // One transform for the WHOLE shot keeps every contact point aligned.
        // Less than one reference pixel, and 0.012 degrees of roll, with overscan.
        const x=.22*Math.sin(time*8.1)+.09*Math.sin(time*17.7);
        const y=.38*Math.sin(time*9.3)+.16*Math.sin(time*21.1);
        const roll=.012*Math.sin(time*5.7);
        return `translate(${x/CarDrive.width*100}%,${y/CarDrive.height*100}%) rotate(${roll}deg) scale(1.004)`;
    }
    render(dt,hidden=false) {
        if(!this.active||!this.motion||!this.ready||hidden)return;
        if(this.reducedMotion&&this.painted)return;
        const step=Math.max(0,Math.min(.1,Number.isFinite(dt)?dt:0));
        if(!this.reducedMotion)this.time+=step;
        this.elapsed+=step;if(this.elapsed<1/20)return;this.elapsed%=1/20;
        const ctx=this.ctx;
        ctx.clearRect(0,0,CarDrive.width,CarDrive.glassHeight);ctx.save();
        for(const x of CarDrive.tilePositions(this.time,this.texture.width))ctx.drawImage(this.texture,x,0);
        ctx.globalCompositeOperation='destination-in';ctx.drawImage(this.mask,0,0);ctx.restore();
        this.layer.style.transform=this.vibration&&!this.reducedMotion?CarDrive.camera(this.time):'';
        this.painted=true;
    }
    dispose(){this.layer.style.transform='';}
}
window.CarDrive=CarDrive;
