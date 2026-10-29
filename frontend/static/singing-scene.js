// One transparent painting, continuously skinned at a neck and an elbow.
// The microphone and its holding hand share one rigid transform. The seated
// upper body inherits one seated sway and breath; the seat stays anchored.
// No pose PNGs, model calls or wall-clock musical choreography are used;
// the host supplies the real recording position. Idle breath uses visible dt.
(() => {
    'use strict';
    const ART = Object.freeze({width:1086,height:1448});
    const STAGE = Object.freeze({width:1448,height:1086});
    const LIMITS = Object.freeze({head:.01,mic:.014,body:.032,lift:10});
    const SEAT = Object.freeze({pivot:[566,1360],rigidUntil:1080});
    const DEFAULT_CALIBRATION = Object.freeze({
        neck:[566,552],head:[545,285,390,345],elbow:[231,1025],
        micCore:[277,457,248,340],micFeather:62,
        shoulder:[235,610,36],torso:[570,760,280,430],restHand:[550,1260,380,188],
        eyes:[[416,351,117,66],[586,315,114,69]],eyeAngles:[-.17,-.30],mouth:[568,451],
        skinSamples:[[475,418],[625,392],[570,426]],
        blush:[[470,418],[635,390]],deskY:730,
        restForearm:[[891,1128],[947,1145],[987,1210],[1007,1280],[1007,1370],[946,1415],
            [874,1406],[819,1400],[750,1410],[684,1430],[648,1441],[616,1435],[592,1444],
            [581,1430],[590,1396],[614,1361],[648,1332],[685,1314],[728,1307],[777,1306],
            [803,1288],[826,1282],[846,1230],[859,1175]]
    });
    const EXPRESSIONS = Object.freeze({
        neutral:{eyeOpen:1,smile:.18,blush:.05,browTilt:0,browLift:0,purse:0},
        singing:{eyeOpen:.67,smile:.78,blush:.38,browTilt:0,browLift:.12,purse:0},
        happy:{eyeOpen:.48,smile:1,blush:.55,browTilt:0,browLift:.35,purse:0},
        shy:{eyeOpen:.69,smile:.6,blush:1,browTilt:-.2,browLift:.08,purse:.18},
        thinking:{eyeOpen:.85,smile:-.04,blush:.05,browTilt:-.38,browLift:.22,purse:.58},
        sad:{eyeOpen:.72,smile:-.85,blush:.04,browTilt:-.85,browLift:.28,purse:.12},
        angry:{eyeOpen:.60,smile:-.65,blush:.28,browTilt:1,browLift:-.25,purse:.28},
        surprised:{eyeOpen:1,smile:.02,blush:.2,browTilt:0,browLift:1,purse:.85}
    });
    const clamp=(value,min,max,fallback=0)=>Math.max(min,Math.min(max,
        typeof value==='number'&&Number.isFinite(value)?value:fallback));
    const smooth=x=>{x=clamp(x,0,1);return x*x*(3-2*x);};
    const nameOf=value=>value==='normal'?'neutral':Object.hasOwn(EXPRESSIONS,value)?value:'neutral';
    const rotate=(point,pivot,angle)=>{
        const x=point[0]-pivot[0],y=point[1]-pivot[1],c=Math.cos(angle),s=Math.sin(angle);
        return [pivot[0]+x*c-y*s,pivot[1]+x*s+y*c];
    };
    const rectDistance=(point,rect)=>Math.hypot(Math.max(rect[0]-point[0],0,point[0]-rect[0]-rect[2]),
        Math.max(rect[1]-point[1],0,point[1]-rect[1]-rect[3]));
    const inRect=(point,rect)=>point[0]>=rect[0]&&point[0]<=rect[0]+rect[2]
        &&point[1]>=rect[1]&&point[1]<=rect[1]+rect[3];

    // Calibrated local axes, not global canvas Y: an open eye is unchanged,
    // and a closed eye collapses along its own normal while retaining width.
    function eyeBasis(angle=0,openness=1) {
        const a=clamp(angle,-.6,.6),o=clamp(openness,0,1,1),c=Math.cos(a),s=Math.sin(a);
        return [c*c+o*s*s,c*s*(1-o),c*s*(1-o),s*s+o*c*c];
    }
    function expressionTarget(name,strength=1) {
        const target=EXPRESSIONS[nameOf(name)],gain=clamp(strength,0,1,1);
        return Object.fromEntries(Object.keys(EXPRESSIONS.neutral).map(key=>
            [key,EXPRESSIONS.neutral[key]+(target[key]-EXPRESSIONS.neutral[key])*gain]));
    }
    function upperLidShape(face,index,width,height) {
        const tilt=clamp(face.browTilt,-1,1),open=clamp(face.eyeOpen,0,1,1),
            amount=smooth((Math.abs(tilt)-.1)/.6),
            center=-height*(.68-.44*amount)*open,
            edge=tilt*(index?-1:1)*height*.20;
        return {amount,center,edge,left:[-width*.43,center-edge],
            control:[0,center-3*amount],right:[width*.43,center+edge]};
    }

    function bodyPose(pose={}) {
        return {angle:clamp(pose.body,-LIMITS.body,LIMITS.body),
            lift:-LIMITS.lift*clamp(pose.breath,-1,1)};
    }

    function applyBodyPose(point,pose={},source=point) {
        // Head, shoulders, microphone and grip inherit exactly the same rigid
        // parent transform. Only the hidden lap blends back into the seat.
        const weight=1-smooth((source[1]-SEAT.rigidUntil)/(SEAT.pivot[1]-SEAT.rigidUntil)),
            parent=bodyPose(pose),moved=rotate(point,SEAT.pivot,parent.angle*weight);
        return [moved[0],moved[1]+parent.lift*weight];
    }

    function skinWeights(point,calibration=DEFAULT_CALIBRATION) {
        // Contact areas always win over expressive influences.
        if(inRect(point,calibration.torso)||inRect(point,calibration.restHand))return {head:0,mic:0,breath:0};
        const shoulder=Math.hypot(point[0]-calibration.shoulder[0],point[1]-calibration.shoulder[1]);
        const neckDistance=Math.hypot(point[0]-calibration.neck[0],point[1]-calibration.neck[1]);
        let mic=1-smooth(rectDistance(point,calibration.micCore)/calibration.micFeather);
        // Fade the arm into its anatomical elbow rather than moving the torso.
        const armFadeStart=calibration.micCore[1]+calibration.micCore[3];
        if(point[1]>armFadeStart)mic*=1-smooth((point[1]-armFadeStart)/(calibration.elbow[1]-armFadeStart));
        mic*=smooth((shoulder-calibration.shoulder[2])/28)*smooth((neckDistance-24)/24);
        // The microphone is beside the face, never an influence on the lips.
        // Include the mouth patch's surrounding mesh vertices, so its skin
        // plate and procedural lip contour receive the identical head matrix.
        mic*=smooth(rectDistance(point,[calibration.mouth[0]-50,calibration.mouth[1]-34,100,68])/30);
        for(const eye of calibration.eyes)mic*=smooth(rectDistance(point,eye)/20);
        const [cx,cy,rx,ry]=calibration.head;
        const radius=Math.hypot((point[0]-cx)/rx,(point[1]-cy)/ry);
        const head=(1-smooth((radius-.84)/.26))*(1-smooth((point[1]-calibration.neck[1])/160))*(1-mic);
        // Clothing-only breathing. Fade to zero before the pinned torso, neck,
        // supporting arm and every microphone/hand influence. It cannot move
        // the seat, grip, face or the hidden free hand.
        const chest=[calibration.neck[0]+32,calibration.neck[1]+145],
            chestRadius=Math.hypot((point[0]-chest[0])/350,(point[1]-chest[1])/220),
            breath=(1-smooth(chestRadius))*smooth(rectDistance(point,calibration.torso)/90)
                *smooth((neckDistance-55)/55)*smooth((shoulder-calibration.shoulder[2])/28)
                *(1-smooth(mic/.08))*(1-smooth(head/.08));
        return {head:clamp(head,0,1),mic:clamp(mic,0,1),breath:clamp(breath,0,1)};
    }

    function deformPoint(point,pose={},calibration=DEFAULT_CALIBRATION,weights=null) {
        weights=weights||skinWeights(point,calibration);
        const h=rotate(point,calibration.neck,clamp(pose.head,-LIMITS.head,LIMITS.head));
        const m=rotate(point,calibration.elbow,clamp(pose.mic,-LIMITS.mic,LIMITS.mic));
        const breath=clamp(pose.breath,-1,1)*clamp(weights.breath,0,1),
            outward=clamp((point[0]-calibration.neck[0])/350,-1,1);
        return applyBodyPose([point[0]+(h[0]-point[0])*weights.head+(m[0]-point[0])*weights.mic+outward*.45*breath,
            point[1]+(h[1]-point[1])*weights.head+(m[1]-point[1])*weights.mic-1.8*breath],pose,point);
    }

    function timelinePose(context={}) {
        if(context.playing!==true||context.hidden||context.reducedMotion)return {head:0,mic:0,body:0};
        const position=clamp(context.position,0,86400),bpm=clamp(context.bpm,30,240,72),
            gain=clamp(context.motionStrength,0,1,.35)*smooth(position/1.2),phase=position*bpm/60*Math.PI;
        return {head:Math.sin(phase*.5)*LIMITS.head*gain,
            mic:Math.sin(phase*.5+.22)*LIMITS.mic*gain,
            body:Math.sin(phase*.5-.18)*LIMITS.body*gain};
    }

    function buildMesh(calibration=DEFAULT_CALIBRATION) {
        // 12 × 16 cells = 384 triangles. Additional feature rows keep the
        // painted hand/mic inside a genuinely rigid, shared set of vertices.
        const xs=[0,150,231,277,325,365,416,458,525,586,700,900,ART.width];
        const ys=[0,150,300,351,416,457,510,552,610,700,797,880,950,1025,1200,1320,ART.height];
        const vertices=[];
        for(const y of ys)for(const x of xs)vertices.push({point:[x,y],weights:skinWeights([x,y],calibration)});
        const triangles=[];
        for(let row=0;row<ys.length-1;row++)for(let col=0;col<xs.length-1;col++) {
            const a=row*xs.length+col,b=a+1,c=a+xs.length,d=c+1;
            triangles.push([a,b,c],[b,d,c]);
        }
        return {vertices,triangles};
    }

    function pairedFace(expression,fallback,playing,mouthOpen,mouthRound) {
        expression=expression&&typeof expression==='object'?expression:null;
        let openness=fallback.eyeOpen;
        if(expression)openness=Math.min(clamp(expression.eyeOpenL??expression.eyeOpen,0,1,1),
            clamp(expression.eyeOpenR??expression.eyeOpen,0,1,1));
        return {eyeOpen:clamp(openness,0,1,1),eyeOpenL:clamp(openness,0,1,1),eyeOpenR:clamp(openness,0,1,1),
            smile:clamp(expression?.smile??fallback.smile,-1,1,.18),
            blush:clamp(expression?.blush??fallback.blush,0,1,.08),
            browTilt:clamp(expression?.browTilt??fallback.browTilt,-1,1),
            browLift:clamp(expression?.browLift??fallback.browLift,-1,1),
            purse:clamp(expression?.purse??fallback.purse,0,1),
            mouthOpen:playing===true?clamp(mouthOpen,0,1):0,
            mouthRound:playing===true?clamp(mouthRound,0,1):0};
    }

    function drawTriangle(ctx,image,source,destination) {
        const [p,q,r]=source,[a,b,c]=destination,
            sx=q[0]-p[0],sy=q[1]-p[1],tx=r[0]-p[0],ty=r[1]-p[1],den=sx*ty-tx*sy;
        if(Math.abs(den)<1e-6)return;
        const ux=b[0]-a[0],uy=b[1]-a[1],vx=c[0]-a[0],vy=c[1]-a[1],
            m11=(ux*ty-vx*sy)/den,m12=(uy*ty-vy*sy)/den,
            m21=(vx*sx-ux*tx)/den,m22=(vy*sx-uy*tx)/den;
        // Both the texture crop and clip need a small shared margin. Expanding
        // only the clip leaves antialiased crop edges visible as a mesh grid.
        const edgeLengths=[Math.hypot(b[0]-c[0],b[1]-c[1]),Math.hypot(a[0]-c[0],a[1]-c[1]),Math.hypot(a[0]-b[0],a[1]-b[1])],
            perimeter=edgeLengths.reduce((n,v)=>n+v,0),radius=Math.abs(ux*vy-vx*uy)/perimeter,
            overlap=1.4,factor=1+overlap/Math.max(radius,.001),
            margin=Math.ceil(overlap*Math.max(...edgeLengths)/Math.max(radius,.001))+2,
            minX=Math.max(0,Math.min(p[0],q[0],r[0])-margin),
            minY=Math.max(0,Math.min(p[1],q[1],r[1])-margin),
            width=Math.min(image.width,Math.max(p[0],q[0],r[0])+margin)-minX,
            height=Math.min(image.height,Math.max(p[1],q[1],r[1])+margin)-minY;
        ctx.save();ctx.beginPath();
        // Expand about the incenter, so every edge gets the same overlap.
        // A radial vertex nudge barely covers the edge of a narrow triangle.
        const cx=(a[0]*edgeLengths[0]+b[0]*edgeLengths[1]+c[0]*edgeLengths[2])/perimeter,
            cy=(a[1]*edgeLengths[0]+b[1]*edgeLengths[1]+c[1]*edgeLengths[2])/perimeter;
        for(let i=0;i<3;i++) {
            const v=destination[i],x=cx+(v[0]-cx)*factor,y=cy+(v[1]-cy)*factor;
            if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
        }
        ctx.closePath();ctx.clip();
        ctx.transform(m11,m12,m21,m22,a[0]-m11*p[0]-m21*p[1],a[1]-m12*p[0]-m22*p[1]);
        ctx.drawImage(image,minX,minY,width,height,minX,minY,width,height);ctx.restore();
    }

    class SingingScene {
        static assetRoot='/static/scenes/singing/';
        static calibration=DEFAULT_CALIBRATION;
        static layout={x:420,y:80,width:560,height:746.6666667};
        static imageCache=new Map();
        constructor(layer,{preview=false,reducedMotion=false,breathing=true,calibration=SingingScene.calibration}={}) {
            this.layer=layer;this.preview=preview;this.reducedMotion=reducedMotion;
            this.canvas=layer.querySelector('.singing-character')||layer.querySelector('.singing-canvas')||layer.querySelector('canvas');
            if(!this.canvas)throw new Error('Singing scene needs a character canvas');
            this.ctx=this.canvas.getContext('2d');this.status=layer.querySelector('[role="status"]');
            this.still=layer.querySelector('.singing-still');this.calibration=calibration;
            this.dolls=layer.querySelector('.singing-dolls');
            this.mesh=buildMesh(calibration);this.layout={...SingingScene.layout};
            this.pose={head:0,mic:0,body:0,breath:0};this.breathingEnabled=breathing!==false;this.breathClock=0;
            this.current={...EXPRESSIONS.neutral};this.expression='neutral';
            this.blinkAge=-1;this.blinkWait=3.4;this.faceAge=0;this.active=false;this.ready=false;this.disposed=false;
            this.onResize=()=>this.resize();
            if(typeof window!=='undefined')window.addEventListener('resize',this.onResize);
        }
        setActive(active,settings={}) {
            if(this.disposed)return;
            this.active=!!active;this.layer.hidden=!this.active;
            this.layer.style?.setProperty('--singing-dim',String(clamp(settings.dim,0,.65)));
            if(typeof settings.breathing_enabled==='boolean')this.setBreathing(settings.breathing_enabled);
            if(this.dolls)this.dolls.hidden=settings.companions===false;
            if(this.active){this.resize();if(!this.loading)this.loading=this.load();}
            else this.ctx.clearRect(0,0,this.canvas.width,this.canvas.height);
        }
        async load() {
            const url=SingingScene.assetRoot+'astra-singer-v2.png';
            if(this.still){this.still.src=url;this.still.hidden=false;}
            if(!SingingScene.imageCache.has(url))SingingScene.imageCache.set(url,new Promise((resolve,reject)=>{
                const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(new Error('Singer artwork unavailable'));image.src=url;
            }));
            try {
                const image=await SingingScene.imageCache.get(url);
                if(this.disposed)return false;
                if(image.naturalWidth!==ART.width||image.naturalHeight!==ART.height)
                    throw new Error('Singer artwork dimensions do not match calibration');
                this.art=image;this.prepareArtwork();this.ready=true;
                this.layer.classList?.add('rig-ready');
                if(this.status)this.status.textContent='';if(this.still)this.still.hidden=true;return true;
            } catch(error) {
                if(!this.disposed&&this.status)this.status.textContent='Singer still artwork · 演唱动画暂不可用，请刷新';
                return false;
            }
        }
        _canvas(width,height){const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;return canvas;}
        _skinPatch(ctx,x,y,rx,ry,color) {
            ctx.save();ctx.translate(x,y);ctx.scale(rx,ry);
            const fill=ctx.createRadialGradient(0,0,.72,0,0,1);fill.addColorStop(0,color);fill.addColorStop(1,color+'00');
            ctx.fillStyle=fill;ctx.fillRect(-1,-1,2,2);ctx.restore();
        }
        prepareArtwork() {
            const body=this._canvas(ART.width,ART.height),ctx=body.getContext('2d',{willReadFrequently:true});
            ctx.drawImage(this.art,0,0);this.eyes=[];
            // Stabilize the runtime texture's almost-opaque interior once.
            // Imagegen's skin/clothes have alpha 252–253; overlapping patches
            // would otherwise accumulate opacity into a visible mesh. Keep
            // hair/wing antialias edges translucent and the source PNG intact.
            const pixels=ctx.getImageData(0,0,ART.width,ART.height);
            for(let i=3;i<pixels.data.length;i+=4)if(pixels.data[i]>=240)pixels.data[i]=255;
            ctx.putImageData(pixels,0,0);
            const sample=([x,y])=>{try {const d=ctx.getImageData(x,y,1,1).data;
                return '#'+[d[0],d[1],d[2]].map(v=>v.toString(16).padStart(2,'0')).join('');
            }catch{return '#f9ddd5';}};
            const colors=this.calibration.skinSamples.map(sample);
            this.calibration.eyes.forEach(([x,y,w,h],index)=>{
                const canvas=this._canvas(w,h),eye=canvas.getContext('2d');
                eye.drawImage(this.art,x,y,w,h,0,0,w,h);
                eye.globalCompositeOperation='destination-in';eye.translate(w/2,h/2);eye.scale(w/2,h/2);
                const mask=eye.createRadialGradient(0,0,.84,0,0,1);mask.addColorStop(0,'#fff');mask.addColorStop(1,'#fff0');
                eye.fillStyle=mask;eye.fillRect(-1,-1,2,2);
                this.eyes.push({canvas,bounds:[x,y,w,h],angle:this.calibration.eyeAngles?.[index]||0});
                this._skinPatch(ctx,x+w/2,y+h/2,w*.55,h*.57,colors[index]);
            });
            this._skinPatch(ctx,...this.calibration.mouth,42,18,colors[2]);this.body=body;
        }
        resize() {
            if(this.disposed)return;
            const layout=typeof getComputedStyle==='function'?getComputedStyle(this.layer):{},
                width=parseFloat(layout.width)||this.layer.clientWidth,height=parseFloat(layout.height)||this.layer.clientHeight;
            if(!(width>0&&height>0))return;
            const device=typeof window!=='undefined'?window.devicePixelRatio||1:1,
                dpr=this.preview?Math.min(device,1.5):Math.min(2160/height,Math.max(device,1080/height));
            this.canvas.width=Math.round(width*dpr);this.canvas.height=Math.round(height*dpr);
            const scale=Math.min(this.canvas.width/STAGE.width,this.canvas.height/STAGE.height);
            this.view=[scale,0,0,scale,(this.canvas.width-STAGE.width*scale)/2,(this.canvas.height-STAGE.height*scale)/2];
            this.ctx.imageSmoothingEnabled=true;this.ctx.imageSmoothingQuality='high';
        }
        setSinging(active,expression='singing'){
            this.singing=!!active;this.returningToRest=!active;
            this.setExpression(active?expression:'neutral');
        }
        setBreathing(enabled){this.breathingEnabled=enabled!==false;if(!this.breathingEnabled)this.pose.breath=0;}
        sampleBreathing(dt,enabled=true) {
            if(!this.breathingEnabled||this.reducedMotion||!enabled)return 0;
            this.breathClock+=clamp(dt,0,.1);
            return Math.sin(this.breathClock*Math.PI*2/4.8);
        }
        setExpression(name) {
            if(this.disposed)return;
            if(name==='blink'){if(this.blinkAge<0)this.blinkAge=0;return;}
            this.expression=nameOf(name);this.faceAge=0;
            if(this.layer.dataset)this.layer.dataset.expression=this.expression;
        }
        setAction(action={}) {
            // No independent limb instruction may break the microphone grip.
            if(action.emotion)this.setExpression(action.emotion);
        }
        sampleFace(dt,expression,playing,mouthOpen,mouthRound) {
            dt=clamp(dt,0,.1);this.faceAge+=dt;
            const semantic=expression&&typeof (expression.expression??expression.emotion)==='string',
                name=semantic?nameOf(expression.expression??expression.emotion):this.singing||this.faceAge<5?this.expression:'neutral',
                strength=expression?.enabled===false?0:semantic?clamp(expression.intensity,0,1,.55):1,
                target=expressionTarget(name,strength),blend=1-Math.exp(-dt*7);
            // Shared FACS magnitudes can be very small on this painted model.
            // Map the same semantic emotion to the calibrated facial channels;
            // preserve its strength, neutral/disabled state and blink clock.
            for(const key of Object.keys(this.current))this.current[key]+=(target[key]-this.current[key])*blend;
            if(!expression)this.blinkWait-=dt;
            if(!expression&&this.blinkAge<0&&this.blinkWait<=0)this.blinkAge=0;
            let closure=0;
            if(this.blinkAge>=0){this.blinkAge+=dt;const age=this.blinkAge;
                closure=age<.065?smooth(age/.065):age<.1?1:1-smooth((age-.1)/.13);
                if(age>=.23){this.blinkAge=-1;this.blinkWait=2.8+Math.random()*3.2;closure=0;}}
            closure=Math.max(closure,clamp(expression?.blink,0,1));
            const current={...this.current,eyeOpen:this.current.eyeOpen*(1-closure)};
            // Older adapters without semantic metadata keep their paired eye
            // values, but an explicit blink must still reach full closure.
            const face=pairedFace(semantic?null:expression,current,playing,mouthOpen,mouthRound);
            if(!semantic&&expression){face.eyeOpen=Math.min(face.eyeOpen,1-closure);face.eyeOpenL=face.eyeOpenR=face.eyeOpen;}
            return face;
        }
        drawFace(face) {
            const ctx=this.ctx;
            for(const [index,{canvas,bounds:[x,y,w,h],angle=0}] of this.eyes.entries()) {
                const openness=face.eyeOpen,closureOffset=h*.10*(1-openness),
                    lid=upperLidShape(face,index,w,h);
                ctx.save();ctx.translate(x+w/2,y+h/2);
                ctx.translate(-Math.sin(angle)*closureOffset,Math.cos(angle)*closureOffset);
                ctx.rotate(angle);
                if(openness>.025){
                    ctx.save();
                    // Move the upper lid, not the eyeball. The clean skin plate
                    // already behind this crop fills the occluded wedge. Both
                    // eyes retain their painted width, centers and local axes.
                    if(lid.amount>.01&&openness>.25){
                        ctx.beginPath();ctx.moveTo(-w*.65,lid.center-lid.edge*1.5);
                        ctx.quadraticCurveTo(0,lid.control[1],w*.65,lid.center+lid.edge*1.5);
                        ctx.lineTo(w*.65,h);ctx.lineTo(-w*.65,h);ctx.closePath();ctx.clip();
                    }
                    ctx.scale(1,openness);ctx.rotate(-angle);ctx.drawImage(canvas,-w/2,-h/2);ctx.restore();
                }
                if(openness<.3){ctx.globalAlpha=1-openness/.3;ctx.strokeStyle='#77536d';ctx.lineWidth=2.7;ctx.lineCap='round';
                    ctx.beginPath();ctx.moveTo(-w*.39,0);ctx.quadraticCurveTo(0,6-face.smile*10,w*.39,0);ctx.stroke();}
                if(openness>.25&&lid.amount>.01){
                    ctx.globalAlpha=lid.amount*.92*smooth((openness-.25)/.2);
                    ctx.strokeStyle='#70485f';ctx.lineWidth=3.8;ctx.lineCap='round';
                    ctx.beginPath();ctx.moveTo(...lid.left);ctx.quadraticCurveTo(...lid.control,...lid.right);ctx.stroke();
                }else if(openness>.25&&Math.abs(face.browLift)>.03){
                    // Surprise uses a small raised crease; the painted brows
                    // stay under the bangs instead of being duplicated/moved.
                    const y=-h*.43*openness-face.browLift*8;
                    ctx.globalAlpha=Math.min(.7,Math.abs(face.browLift)*.45);
                    ctx.strokeStyle='#896378';ctx.lineWidth=2.8;ctx.lineCap='round';
                    ctx.beginPath();ctx.moveTo(-w*.30,y);ctx.quadraticCurveTo(0,y-4,w*.30,y);ctx.stroke();
                }
                ctx.restore();
            }
            if(face.blush>.01)for(const [x,y] of this.calibration.blush){
                ctx.save();ctx.translate(x,y);ctx.scale(40,14);
                const fill=ctx.createRadialGradient(0,0,0,0,0,1);fill.addColorStop(0,`rgba(236,104,142,${face.blush*.36})`);fill.addColorStop(1,'#ec749700');
                ctx.fillStyle=fill;ctx.fillRect(-1,-1,2,2);ctx.restore();
            }
            this.drawMouth(face.mouthOpen,face.mouthRound,face.smile,face.purse);
        }
        drawMouth(open,round,smile,purse=0) {
            const ctx=this.ctx,[x,y]=this.calibration.mouth,half=20+Math.max(0,smile)*5-round*5-purse*9;
            ctx.save();ctx.translate(x,y);ctx.rotate(-.11);ctx.lineWidth=1.8;ctx.lineCap='round';ctx.strokeStyle='#ac6e78';ctx.beginPath();
            if(open<.025){ctx.moveTo(-half,-1);ctx.quadraticCurveTo(0,smile*(smile<0?18:13),half,-1);ctx.stroke();}
            else {const height=3+open*17;ctx.moveTo(-half,-1);ctx.quadraticCurveTo(0,-2-open*2,half,-1);
                ctx.bezierCurveTo(half*.82,height,-half*.82,height,-half,-1);ctx.closePath();ctx.fillStyle='#794854';ctx.fill();ctx.stroke();ctx.clip();
                ctx.fillStyle='#df9ca5';ctx.beginPath();ctx.ellipse(0,height,half*.74,height*.36,0,0,Math.PI*2);ctx.fill();
                if(open>.35){ctx.fillStyle='#fff0e6';ctx.fillRect(-half,-3,half*2,3);}}
            ctx.restore();
        }
        drawBody() {
            const ctx=this.ctx,layout=this.layout,
                deskSource=(this.calibration.deskY-layout.y)*ART.height/layout.height;
            // The continuous tabletop occludes the lap and the non-mic hand.
            // No foreground arm crop may reveal skin/clothes below this edge.
            ctx.save();ctx.beginPath();ctx.rect(-100,-100,ART.width+200,Math.max(0,deskSource)+100);ctx.clip();
            if(Math.abs(this.pose.head)+Math.abs(this.pose.mic)+Math.abs(this.pose.body||0)+Math.abs(this.pose.breath||0)<1e-7)ctx.drawImage(this.body,0,0);
            else {
                const points=this.mesh.vertices.map(v=>deformPoint(v.point,this.pose,this.calibration,v.weights));
                for(const triangle of this.mesh.triangles)drawTriangle(ctx,this.body,
                    triangle.map(i=>this.mesh.vertices[i].point),triangle.map(i=>points[i]));
            }
            ctx.restore();
        }
        render(params={},dt=0,hidden=false,context={}) {
            if(!this.active||!this.ready||this.disposed||hidden)return;
            const playing=context.playing===true;
            if(this.reducedMotion)this.pose={head:0,mic:0,body:0,breath:0};
            else if(playing)this.pose=timelinePose({...context,playing:true});
            else if(this.returningToRest){
                const decay=Math.exp(-clamp(dt,0,.1)*5);
                for(const key of ['head','mic','body'])this.pose[key]=Math.abs(this.pose[key]||0)<1e-6?0:this.pose[key]*decay;
            }
            this.pose.breath=this.sampleBreathing(dt,context.breathing!==false);
            // Otherwise retain the exact last joint positions: pausing does not
            // snap the hand, microphone or head back to a different PNG/pose.
            const voicePlaying=typeof context.voicePlaying==='boolean'?context.voicePlaying:playing,
                face=this.sampleFace(dt,params.expressionState,voicePlaying,params.mouthOpen,params.mouthRound),ctx=this.ctx;
            ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,this.canvas.width,this.canvas.height);
            ctx.setTransform(...(this.view||[1,0,0,1,0,0]));
            const layout=this.layout;ctx.save();ctx.translate(layout.x,layout.y);ctx.scale(layout.width/ART.width,layout.height/ART.height);
            this.drawBody();
            const [nx,ny]=this.calibration.neck,parent=bodyPose(this.pose);ctx.save();
            ctx.translate(0,parent.lift);ctx.translate(...SEAT.pivot);ctx.rotate(parent.angle);ctx.translate(-SEAT.pivot[0],-SEAT.pivot[1]);
            ctx.translate(nx,ny);ctx.rotate(this.pose.head);ctx.translate(-nx,-ny);
            this.drawFace(face);ctx.restore();ctx.restore();
        }
        dispose() {
            this.disposed=true;this.active=false;
            this.layer.classList?.remove('rig-ready');
            if(typeof window!=='undefined')window.removeEventListener('resize',this.onResize);
            this.ctx.clearRect(0,0,this.canvas.width,this.canvas.height);this.body=null;this.eyes=[];
        }
    }
    if(typeof window!=='undefined')window.SingingScene=SingingScene;
    if(typeof module!=='undefined'&&module.exports)module.exports={SingingScene,ART,STAGE,LIMITS,SEAT,bodyPose,applyBodyPose,
        DEFAULT_CALIBRATION,EXPRESSIONS,skinWeights,deformPoint,timelinePose,buildMesh,pairedFace,rotate,drawTriangle,eyeBasis,expressionTarget,upperLidShape};
})();
