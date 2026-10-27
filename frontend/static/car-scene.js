// A fixed, painted pose preserves every contact point: seat, wheel, wrists and
// dashboard companions. Only local facial features animate; no full PNG swaps.
class CarScene {
    static expressions = {
        normal:    {open:1,    smile:.22, blush:.04, tilt:0,     pout:0,   asym:0,   restOpen:0},
        singing:   {open:.42,  smile:.65, blush:.30, tilt:0,     pout:0,   asym:0,   restOpen:0},
        happy:     {open:.70,  smile:1,   blush:.38, tilt:0,     pout:0,   asym:0,   restOpen:0},
        shy:       {open:.80,  smile:.48, blush:.85, tilt:0,     pout:0,   asym:0,   restOpen:0},
        sad:       {open:.74,  smile:-.6, blush:.08, tilt:-.065, pout:0,   asym:0,   restOpen:0},
        angry:     {open:.65,  smile:-.3, blush:.30, tilt:.10,   pout:.55, asym:0,   restOpen:0},
        thinking:  {open:.88,  smile:0,   blush:.04, tilt:0,     pout:.12, asym:.12, restOpen:0},
        surprised: {open:1.08, smile:.15, blush:.20, tilt:0,     pout:.8,  asym:0,   restOpen:.22},
        smug:      {open:.73,  smile:.66, blush:.32, tilt:0,     pout:0,   asym:.10, restOpen:0}
    };
    static assetRoot = '/static/scenes/car/';
    constructor(layer, {preview=false, reducedMotion=false}={}) {
        this.layer=layer;this.canvas=layer.querySelector('.car-face');
        this.ctx=this.canvas.getContext('2d');this.image=layer.querySelector('img');
        this.status=layer.querySelector('[role="status"]');
        this.preview=preview;this.reducedMotion=reducedMotion;
        this.drive=window.CarDrive?new CarDrive(layer,{preview,reducedMotion}):null;
        this.blinkClock=new CharacterFace();this.current={...CarScene.expressions.normal};
        this.expression='normal';this.expressionAge=0;this.active=false;this.ready=false;
        this.onResize=()=>this.resize();window.addEventListener('resize',this.onResize);
    }
    setActive(active, settings={}) {
        this.active=!!active;this.layer.hidden=!this.active;
        this.drive?.setActive(this.active,settings);
        this.dim=Math.max(0,Math.min(.65,settings.dim||0));
        this.layer.style.setProperty('--car-dim',String(this.dim));
        if(this.active) {
            this.resize();
            if(!this.loading)this.loading=this.load();
        } else this.ctx.clearRect(0,0,this.canvas.width,this.canvas.height);
    }
    async load() {
        const image=url=>new Promise((resolve,reject)=>{
            const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error('Car artwork unavailable'));img.src=url;
        });
        // The visible still is also the graceful fallback if the animation plate fails.
        this.image.src=CarScene.assetRoot+'astra-car-v1.png';
        this.image.onerror=()=>{this.status.textContent='Car artwork unavailable · 请刷新重试';};
        try {
            const [art,clean]=await Promise.all([image(this.image.src),image(CarScene.assetRoot+'astra-car-clean-v1.png')]);
            this.art=art;this.clean=clean;
            this.plate=this.crop(clean,[365,215,340,230],.93);
            this.eyes=[[395,270,115,71],[557,248,108,73]].map(bounds=>({bounds,canvas:this.crop(art,bounds,.92)}));
            this.ready=true;this.status.textContent='';
        } catch(error) {
            this.status.textContent='Still artwork · 面部动画加载失败，请刷新';
            console.warn(error.message);
        }
    }
    crop(image,[x,y,w,h],inner=.8) {
        const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
        const ctx=canvas.getContext('2d');ctx.drawImage(image,x,y,w,h,0,0,w,h);
        ctx.globalCompositeOperation='destination-in';ctx.translate(w/2,h/2);ctx.scale(w/2,h/2);
        const fade=ctx.createRadialGradient(0,0,inner,0,0,1);fade.addColorStop(0,'#fff');fade.addColorStop(1,'#fff0');
        ctx.fillStyle=fade;ctx.fillRect(-1,-1,2,2);return canvas;
    }
    resize() {
        const layout=getComputedStyle(this.layer);
        const w=parseFloat(layout.width)||this.layer.clientWidth,h=parseFloat(layout.height)||this.layer.clientHeight;if(!w||!h)return;
        const dpr=this.preview?Math.min(window.devicePixelRatio||1,1.5):Math.min(2160/h,Math.max(window.devicePixelRatio||1,1080/h));
        this.canvas.width=Math.round(w*dpr);this.canvas.height=Math.round(h*dpr);
        // Landmarks are measured in the original 1448 × 1086 illustration.
        this.ctx.setTransform(this.canvas.width/1448,0,0,this.canvas.height/1086,0,0);
        this.ctx.imageSmoothingEnabled=true;this.ctx.imageSmoothingQuality='high';
        this.drive?.resize();
    }
    setExpression(name) {
        if(name==='blink'){this.blinkClock.blink();return;}
        this.expression=Object.hasOwn(CarScene.expressions,name)?name:'normal';this.expressionAge=0;
        this.layer.dataset.expression=this.expression;
    }
    setSinging(active,expression='singing'){this.singing=!!active;this.setExpression(active?expression:'normal');}
    setAction(action={}) {
        const names={shy:'shy',heart:'shy',laugh:'happy',present:'happy',think:'thinking',cry:'sad',angry:'angry',surprise:'surprised'};
        if(names[action.name])this.setExpression(names[action.name]);
    }
    sample(dt) {
        dt=Math.max(0,Math.min(.1,Number.isFinite(dt)?dt:0));this.expressionAge+=dt;
        const target=CarScene.expressions[this.singing||this.expressionAge<8?this.expression:'normal'];
        const blend=1-Math.exp(-dt*8);
        for(const key of Object.keys(this.current))this.current[key]+=(target[key]-this.current[key])*blend;
        const blink=this.blinkClock.sample(dt).blink;
        return {...this.current,blink};
    }
    render(params={},dt=0,hidden=false) {
        this.drive?.render(dt,hidden);
        if(!this.active||!this.ready||hidden)return;
        const f=this.sample(dt),ctx=this.ctx;
        ctx.clearRect(360,210,350,240);
        ctx.drawImage(this.plate,365,215);
        this.eyes.forEach((eye,index)=>this.drawEye(eye,index,f));
        this.drawBlush(f.blush);
        // The client supplies the real audio envelope, including silence, pause
        // and mute. Idle actions must never invent lip movement.
        const mouth=Number.isFinite(params.mouthOpen)?Math.max(0,Math.min(1,params.mouthOpen)):0;
        this.drawMouth(mouth,Math.max(0,Math.min(1,params.mouthRound||0)),f);
    }
    drawEye(eye,index,f) {
        const ctx=this.ctx,[x,y,w,h]=eye.bounds,cx=x+w/2,cy=y+h/2;
        const openness=Math.max(0,Math.min(1.1,(f.open+(index?f.asym:-f.asym))*(1-f.blink)));
        ctx.save();ctx.translate(cx,cy);ctx.rotate((index?-1:1)*f.tilt);
        if(openness>.035){ctx.save();ctx.scale(1,openness);ctx.drawImage(eye.canvas,-w/2,-h/2);ctx.restore();}
        if(openness<.3) {
            ctx.globalAlpha=1-Math.min(1,openness/.3);ctx.strokeStyle='#6b4d61';ctx.lineWidth=2.8;ctx.lineCap='round';
            ctx.beginPath();ctx.moveTo(-w*.37,0);ctx.quadraticCurveTo(0,9+f.smile*3,w*.35,-2);ctx.stroke();
            ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(-w*.32,1);ctx.lineTo(-w*.39,-2);ctx.stroke();
        }
        ctx.restore();
    }
    drawBlush(amount) {
        if(amount<.01)return;
        const ctx=this.ctx;
        for(const [x,y] of [[450,343],[603,326]]) {
            ctx.save();ctx.translate(x,y);ctx.rotate(-.1);ctx.scale(28,12);
            const fill=ctx.createRadialGradient(0,0,0,0,0,1);fill.addColorStop(0,`rgba(235,112,146,${amount*.26})`);fill.addColorStop(1,'#eb709200');
            ctx.fillStyle=fill;ctx.fillRect(-1,-1,2,2);ctx.restore();
        }
    }
    drawMouth(open,round,f) {
        open=Math.max(open,f.restOpen);round=Math.max(round,f.restOpen*3);
        const ctx=this.ctx,half=19+f.smile*4-round*5-f.pout*5;
        ctx.save();ctx.translate(542,378);ctx.rotate(-.10);ctx.strokeStyle='#ac6977';ctx.lineWidth=1.5;ctx.lineCap='round';
        ctx.beginPath();
        if(open<.025) {
            ctx.moveTo(-half,-1);ctx.quadraticCurveTo(0,2+f.smile*8,half,-1);ctx.stroke();
        } else {
            const h=3+open*20;
            ctx.moveTo(-half,-1);ctx.quadraticCurveTo(0,-3-open*3,half,-1);
            ctx.bezierCurveTo(half*.85,h,-half*.85,h,-half,-1);ctx.closePath();
            ctx.fillStyle='#794657';ctx.fill();ctx.stroke();ctx.clip();
            ctx.fillStyle='#e09ba8';ctx.beginPath();ctx.ellipse(0,h,half*.77,h*.39,0,0,Math.PI*2);ctx.fill();
            if(open>.35){ctx.fillStyle='#fff1e9';ctx.fillRect(-half,-3,half*2,3.5);}
        }
        ctx.restore();
    }
    dispose(){window.removeEventListener('resize',this.onResize);this.drive?.dispose();}
}
window.CarScene=CarScene;
