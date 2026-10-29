// Static local artwork + bounded Canvas weather. No video decoder or network loop.
class StageScene {
    static backgrounds={moonlit:'moonlit-terrace.png',palace:'royal-palace.png',garden:'rose-garden.png'};
    static defaults={background:'moonlit',effect:'clouds',weather_amount:.45,dim:.10,
        companions:true,car_motion:true,car_vibration:true,bottom_blur:4,character_x:0,character_y:0,character_scale:1,
        subtitle_seconds:4,music_enabled:true,music_source:'file',music_volume:.18,background_asset:null,music_asset:null};
    static normalize(value={}) {
        const number=(key,min,max)=>Number.isFinite(value[key])?Math.max(min,Math.min(max,value[key])):StageScene.defaults[key];
        const asset=key=>typeof value[key]==='string'&&/^[a-f0-9]{32}$/.test(value[key])?value[key]:null;
        return {background:['moonlit','palace','garden','car','singing-room','transparent','custom'].includes(value.background)?value.background:'moonlit',
            background_asset:asset('background_asset'),music_asset:asset('music_asset'),
            effect:['rain','clouds','sparkles','none'].includes(value.effect)?value.effect:'clouds',
            weather_amount:number('weather_amount',0,1),dim:number('dim',0,.65),
            companions:typeof value.companions==='boolean'?value.companions:StageScene.defaults.companions,
            car_motion:typeof value.car_motion==='boolean'?value.car_motion:true,
            car_vibration:typeof value.car_vibration==='boolean'?value.car_vibration:true,
            bottom_blur:number('bottom_blur',0,12),
            character_x:number('character_x',-45,45),character_y:number('character_y',-35,35),
            character_scale:number('character_scale',.4,2),
            subtitle_seconds:number('subtitle_seconds',1,20),
            music_enabled:typeof value.music_enabled==='boolean'?value.music_enabled:StageScene.defaults.music_enabled,
            music_source:value.music_source==='system'?'system':'file',
            music_volume:number('music_volume',0,.4)};
    }
    static placement(value) {
        const s=StageScene.normalize(value);
        return `translate(${s.character_x}%, ${s.character_y}%) scale(${s.character_scale})`;
    }
    constructor(layer,canvas,character,{transparent=false,reducedMotion=false,companions=null,bottomBlur=null}={}) {
        this.layer=layer;this.canvas=canvas;this.character=character;this.ctx=canvas.getContext('2d');
        this.companions=companions;this.bottomBlur=bottomBlur;
        this.forceTransparent=transparent;this.reducedMotion=reducedMotion;this.time=0;this.elapsed=0;
        this.drops=Array.from({length:150},(_,i)=>({x:this.noise(i+1),y:this.noise(i+201),depth:.4+this.noise(i+401)*.6}));
        this.cloud=this.cloudTexture();
        this.onResize=()=>this.resize();window.addEventListener('resize',this.onResize);
        this.setSettings(StageScene.defaults);this.resize();
    }
    noise(n){return ((Math.sin(n*127.1)*43758.5453)%1+1)%1;}
    cloudTexture() {
        const cloud=document.createElement('canvas');cloud.width=512;cloud.height=160;
        const ctx=cloud.getContext('2d');
        for(let i=0;i<8;i++) {
            const x=70+i*52,y=82+Math.sin(i*2)*12,r=45+this.noise(i+801)*35;
            const fill=ctx.createRadialGradient(x,y,0,x,y,r);
            fill.addColorStop(0,'#c7c8e54a');fill.addColorStop(.45,'#b6badd24');fill.addColorStop(1,'#aeb5d600');
            ctx.fillStyle=fill;ctx.fillRect(x-r,y-r,r*2,r*2);
        }
        return cloud;
    }
    setSettings(value) {
        this.settings=StageScene.normalize(value);
        const s=this.settings;
        this.transparent=this.forceTransparent||s.background==='transparent';
        this.car=s.background==='car'&&!this.forceTransparent;
        this.singingRoom=s.background==='singing-room'&&!this.forceTransparent;
        this.integrated=this.car||this.singingRoom;
        this.layer.hidden=this.transparent||this.integrated;
        this.character.style.visibility=this.integrated?'hidden':'';
        const artwork=s.background==='custom'&&s.background_asset?`/media/${s.background_asset}.png`
            :StageScene.backgrounds[s.background]?`/static/scenes/${StageScene.backgrounds[s.background]}`:null;
        this.layer.style.setProperty('--scene-art',artwork?`url("${artwork}")`:'none');
        this.layer.style.setProperty('--scene-dim',String(s.dim));
        if(this.companions)this.companions.hidden=this.transparent||this.integrated||!s.companions;
        if(this.bottomBlur) {
            this.bottomBlur.hidden=this.transparent||s.bottom_blur===0;
            this.bottomBlur.style.setProperty('--bottom-blur',`${s.bottom_blur}px`);
        }
        this.character.style.transform=this.integrated?'none':StageScene.placement(s);
        this.elapsed=1;this.clear();
        if(this.width)this.draw();
    }
    resize() {
        // Layout sizes do not include the character's CSS transform.
        this.width=this.canvas.clientWidth||window.innerWidth;
        this.height=this.canvas.clientHeight||window.innerHeight;
        const dpr=Math.min(window.devicePixelRatio||1,1.25);
        this.canvas.width=Math.round(this.width*dpr);this.canvas.height=Math.round(this.height*dpr);
        this.ctx.setTransform(dpr,0,0,dpr,0,0);this.elapsed=1;this.draw();
    }
    clear(){this.ctx.clearRect(0,0,this.width||0,this.height||0);}
    render(dt,hidden=false) {
        if(this.integrated||this.transparent||this.reducedMotion||hidden||this.settings.effect==='none'||this.settings.weather_amount===0)return;
        const step=Math.max(0,Math.min(Number.isFinite(dt)?dt:0,.1));
        this.time+=step;this.elapsed+=step;
        if(this.elapsed<1/20)return;this.elapsed%=1/20;this.draw();
    }
    draw() {
        this.clear();
        if(!this.width||this.integrated||this.transparent||this.reducedMotion)return;
        const {effect,weather_amount:amount}=this.settings;if(effect==='none'||amount===0)return;
        const ctx=this.ctx,w=this.width,h=this.height,t=this.time;
        ctx.save();
        if(effect==='rain') {
            // Rain stays behind the character. A short lower fade avoids streaks
            // ending abruptly on the terrace edge; the scene has no added sound.
            const count=Math.round(150*amount);
            for(let i=0;i<count;i++) {
                const drop=this.drops[i],phase=(drop.y+t*(.30+drop.depth*.28))%1;
                const x=((drop.x-t*.025*drop.depth)%1+1)%1*w,y=phase*h;
                const length=(9+drop.depth*13)*h/720;
                ctx.globalAlpha=(.12+drop.depth*.22)*(1-Math.max(0,(phase-.82)/.18));
                ctx.strokeStyle='#d6dfef';ctx.lineWidth=.5+drop.depth*.6;
                ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x-3*drop.depth,y+length);ctx.stroke();
            }
        } else if(effect==='sparkles') {
            // Quiet golden motes suit an indoor palace; at most 40 tiny dots.
            for(let i=0;i<Math.round(40*amount);i++) {
                const p=this.drops[i],phase=(p.y-t*.012*p.depth+100)%1;
                ctx.globalAlpha=(.18+.28*Math.sin(t*.7+i)**2)*Math.sin(phase*Math.PI);
                ctx.fillStyle='#ffe7b4';ctx.beginPath();
                ctx.arc((p.x+Math.sin(t*.3+i)*.007)*w,phase*h,(.8+p.depth)*h/720,0,Math.PI*2);ctx.fill();
            }
        } else {
            // A cached cloud sprite drifts continuously through the distant sky.
            const cw=w*.60,ch=h*.22;
            for(let i=0;i<5;i++) {
                const x=((i*.29+t*(.0025+i*.0003))%1.7-.7)*w;
                ctx.globalAlpha=(.35+amount*.65)*(i%2?.7:1);
                ctx.drawImage(this.cloud,x,h*(.08+(i%3)*.105),cw,ch);
            }
        }
        ctx.restore();
    }
    dispose(){window.removeEventListener('resize',this.onResize);this.clear();}
}
window.StageScene=StageScene;
