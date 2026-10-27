// A layered Canvas rig: shoulders -> elbows -> hands, torso -> neck/head,
// and independent wing roots. Every frame uses the same painted parts.
class CutoutRig {
    constructor(image, definition, bodyImage) {
        this.image = image;
        this.definition = definition;
        this.parts = {};
        for (const [name, part] of Object.entries(definition.parts)) {
            const source = part.source === 'body' ? bodyImage : image;
            this.parts[name] = {...part, ...this.extractPart(part.cell, source)};
        }
        this.prepareFace();
    }

    static async load(url) {
        const response = await fetch(url);
        if (!response.ok) throw new Error('2D rig definition unavailable');
        const definition = await response.json();
        const loadImage = source => new Promise((resolve,reject) => {
            const image = new Image(); image.onload=()=>resolve(image); image.onerror=reject;
            image.src=new URL(source,new URL(url,location.href)).href;
        });
        const [image,body] = await Promise.all([loadImage(definition.atlas),loadImage(definition.body)]);
        return new CutoutRig(image, definition, body);
    }

    extractPart(cell, image) {
        const [x,y,w,h] = cell.map((value,index) => Math.round(value * (index % 2 ? image.height : image.width)));
        const canvas = document.createElement('canvas'); canvas.width = w; canvas.height = h;
        const context = canvas.getContext('2d', {willReadFrequently:true});
        context.drawImage(image, x,y,w,h, 0,0,w,h);
        const bitmap = context.getImageData(0,0,w,h), pixels=bitmap.data;
        // An atlas cell may contain a few detached flecks from a neighbouring
        // part. Keep its largest connected alpha island in this one-time cache.
        const labels=new Uint32Array(w*h), queue=new Uint32Array(w*h);
        let label=0, largest=0, best=0;
        for(let start=0;start<labels.length;start++) {
            if(labels[start] || pixels[start*4+3]<24) continue;
            label++; let read=0, write=1; queue[0]=start;labels[start]=label;
            while(read<write) {
                const at=queue[read++], px=at%w;
                for(let direction=0;direction<4;direction++) {
                    const next=direction===0?(px?at-1:-1):direction===1?(px<w-1?at+1:-1):direction===2?at-w:at+w;
                    if(next<0 || next>=labels.length || labels[next] || pixels[next*4+3]<24) continue;
                    labels[next]=label;queue[write++]=next;
                }
            }
            if(write>largest){largest=write;best=label;}
        }
        let left=w, top=h, right=0, bottom=0;
        for (let py=0; py<h; py++) for (let px=0; px<w; px++) {
            if (labels[py*w+px]!==best) { pixels[(py*w+px)*4+3]=0; continue; }
            left=Math.min(left,px); top=Math.min(top,py); right=Math.max(right,px+1); bottom=Math.max(bottom,py+1);
        }
        if (right <= left || bottom <= top) throw new Error('Empty 2D rig part');
        context.putImageData(bitmap,0,0);
        return {source:canvas,bounds:[left,top,right-left,bottom-top]};
    }

    piece(ctx, name, x, y, angle=0, scaleX=1, scaleY=1, clip=null) {
        const part = this.parts[name];
        if (!part) return;
        const [sx,sy,sw,sh] = part.bounds;
        const height = part.height, width = height*sw/sh;
        const left = -part.pivot[0]*width, top = -part.pivot[1]*height;
        ctx.save(); ctx.translate(x,y); ctx.rotate(angle); ctx.scale(scaleX,scaleY);
        if (clip) { ctx.beginPath(); ctx.rect(-1200,clip[0],2400,clip[1]-clip[0]); ctx.clip(); }
        ctx.drawImage(part.source,sx,sy,sw,sh,left,top,width,height);
        ctx.restore();
    }

    arm(ctx, side, pose) {
        const sign = side === 'left' ? -1 : 1;
        const shoulder = pose[`${side}Shoulder`];
        const elbow = pose[`${side}Elbow`];
        ctx.save(); ctx.translate(500+sign*99,355); ctx.rotate(shoulder);
        // The painted upper arm slopes outward. Attach to its anatomical elbow,
        // not to an imaginary vertical bone. Keep the wrist/hand intact: this
        // artwork has no overlapping wrist cap suitable for independent rotation.
        ctx.save(); ctx.translate(sign*38,155); ctx.rotate(elbow);
        this.piece(ctx,`${side}Forearm`,0,0);
        ctx.restore();
        this.piece(ctx,`${side}Upper`,0,0);
        ctx.restore();
    }

    prepareFace() {
        if(!this.definition.face) return;
        // The atlas crop ends across a few hair tips. Feather only that bottom
        // edge once so it blends into the back-hair layer, without a straight seam.
        const head=this.parts.head,cache=head.source.getContext('2d');
        const bottom=head.bounds[1]+head.bounds[3];
        cache.save();cache.globalCompositeOperation='destination-in';
        const fade=cache.createLinearGradient(0,bottom-20,0,bottom);
        fade.addColorStop(0,'#fff');fade.addColorStop(1,'#fff0');
        cache.fillStyle=fade;cache.fillRect(0,0,head.source.width,head.source.height);cache.restore();
        this.eyes=this.definition.face.eyes.map(([x,y,w,h])=>{
            const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
            const ctx=canvas.getContext('2d');ctx.drawImage(this.image,x,y,w,h,0,0,w,h);
            // Feather the skin at the edges; the original painted iris/lashes remain.
            ctx.globalCompositeOperation='destination-in';
            ctx.translate(w/2,h/2);ctx.scale(w/2,h/2);
            const mask=ctx.createRadialGradient(0,0,.65,0,0,1);
            mask.addColorStop(0,'#fff');mask.addColorStop(1,'#fff0');
            ctx.fillStyle=mask;ctx.fillRect(-1,-1,2,2);
            return {canvas,x,y,w,h};
        });
    }

    skinPatch(ctx,x,y,rx,ry,color) {
        ctx.save();ctx.translate(x,y);ctx.scale(rx,ry);
        const fill=ctx.createRadialGradient(0,0,.72,0,0,1);
        fill.addColorStop(0,color);fill.addColorStop(1,color+'00');
        ctx.fillStyle=fill;ctx.fillRect(-1,-1,2,2);ctx.restore();
    }

    drawFace(ctx,face={},mouth=0,round=0) {
        if(!this.definition.face || !this.eyes) return;
        const part=this.parts.head,[bx,by,bw,bh]=part.bounds,k=part.height/bh;
        const ox=Math.round(part.cell[0]*this.image.width)+bx;
        const oy=Math.round(part.cell[1]*this.image.height)+by;
        ctx.save();ctx.translate(-part.pivot[0]*bw*k,-part.pivot[1]*part.height);
        ctx.scale(k,k);ctx.translate(-ox,-oy);
        const openness=Math.max(0,Math.min(1.05,face.eyeOpen??1));
        for(const eye of this.eyes) {
            const cx=eye.x+eye.w/2,cy=eye.y+eye.h/2;
            this.skinPatch(ctx,cx,cy,eye.w*.57,eye.h*.67,'#f5d8c7');
            if(openness>.08) {
                ctx.save();ctx.translate(cx,cy);ctx.scale(1,openness);
                ctx.drawImage(eye.canvas,-eye.w/2,-eye.h/2);ctx.restore();
            }
            if(openness<.4) {
                ctx.save();ctx.globalAlpha=1-Math.min(1,openness/.4);
                ctx.strokeStyle='#6c5260';ctx.lineWidth=1.8;ctx.lineCap='round';
                ctx.beginPath();ctx.moveTo(cx-20,cy-1);
                ctx.quadraticCurveTo(cx,cy+7+(face.smile||0)*2,cx+20,cy-1);ctx.stroke();
                ctx.restore();
            }
        }
        const blush=Math.max(0,Math.min(1,face.blush||0));
        for(const x of [773,845]) {
            ctx.save();ctx.globalAlpha=blush*.25;
            this.skinPatch(ctx,x,239,19,8,'#ea8ea3');ctx.restore();
        }
        // Landmarks are in the ORIGINAL atlas pixels, so they follow every head transform.
        const [mx,my]=this.definition.face.mouth;
        this.skinPatch(ctx,mx,my,22,12,'#fae2d1');
        const open=Math.max(0,Math.min(1,mouth)),smile=face.smile??.2;
        const half=10+smile*3-round*3;
        ctx.strokeStyle='#a56d78';ctx.lineWidth=1.1;ctx.lineCap='round';
        ctx.beginPath();ctx.moveTo(mx-half,my-1);
        if(open<.025) {
            ctx.quadraticCurveTo(mx,my+2+smile*4,mx+half,my-1);ctx.stroke();
        } else {
            const h=1+open*12;
            ctx.quadraticCurveTo(mx,my-2-open*2,mx+half,my-1);
            ctx.bezierCurveTo(mx+half*.8,my+h,mx-half*.8,my+h,mx-half,my-1);
            ctx.closePath();ctx.fillStyle='#704753';ctx.fill();ctx.stroke();
            ctx.save();ctx.clip();ctx.fillStyle='#d995a2';ctx.beginPath();
            ctx.ellipse(mx,my+h,half*.7,h*.35,0,0,Math.PI*2);ctx.fill();ctx.restore();
        }
        ctx.restore();
    }

    draw(ctx, width, height, pose, mouth=0, reduced=false, face={}, round=0, portrait=false) {
        const scale = Math.min(width/1100,height/1570)*0.94;
        const zoom=portrait?Math.min(width/420,height/480):scale;
        ctx.save(); ctx.translate(width/2-500*zoom,height/2-(portrait?275:750)*zoom);
        ctx.scale(zoom,zoom);
        ctx.translate(500+pose.x*1000,1410+pose.y*1500);
        ctx.rotate(pose.roll); ctx.scale(1+pose.pulse,1+pose.pulse-pose.bow*0.35);
        ctx.translate(-500,-1410);
        // The skirt stays planted; the upper body rotates at the waist.
        this.piece(ctx,'body',500,320,0,1,1,[300,1300]);
        ctx.save(); ctx.translate(500,675); ctx.rotate(pose.torso); ctx.translate(-500,-675);
        const flutter = reduced ? 0 : pose.flutter;
        this.piece(ctx,'leftWing',442,440,-pose.wingSpread-flutter,1+flutter*1.6,1);
        this.piece(ctx,'rightWing',558,440,pose.wingSpread+flutter,1+flutter*1.6,1);
        this.piece(ctx,'hair',500,160,pose.headRoll*0.35+pose.hairSway);
        this.piece(ctx,'body',500,320,0,1,1,[-100,325]);
        this.arm(ctx,'left',pose); this.arm(ctx,'right',pose);
        ctx.save(); ctx.translate(500+pose.headYaw*35,337+pose.headPitch*18);
        ctx.rotate(pose.headRoll); ctx.scale(1,1-Math.abs(pose.headPitch)*0.12);
        this.piece(ctx,'head',0,0);
        this.drawFace(ctx,face,mouth,round);
        ctx.restore();
        ctx.restore(); ctx.restore();
    }
}
window.CutoutRig = CutoutRig;
