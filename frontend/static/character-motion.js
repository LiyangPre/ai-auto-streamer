// Conservative front-view choreography. Feet and dress remain planted.
class CharacterMotion {
    static durations={idle:0,nod:2.8,think:4,shy:4,laugh:3.2,present:4};
    static aliases={breathe:'idle',talk:'idle',wave:'present',sway:'present',tilt:'think',
        bounce:'laugh',slump:'think',shake:'think',pop:'nod',lean:'nod',heart:'shy',cheer:'laugh',
        double_wave:'present',peek:'shy',stretch:'idle',curtsy:'nod',bow:'nod',cry:'think',angry:'think',surprise:'nod'};
    // Limits for this artwork, deliberately much smaller than human range of motion.
    static limits={leftShoulder:[0,.24],rightShoulder:[-.24,0],leftElbow:[-.14,0],
        rightElbow:[0,.14],leftWrist:[0,0],rightWrist:[0,0],headRoll:[-.065,.065],
        headPitch:[-.02,.055],headYaw:[-.018,.018],hairSway:[-.009,.009],flutter:[-.004,.004],
        x:[0,0],y:[0,0],roll:[0,0],bow:[0,0],pulse:[0,0],torso:[0,0],wingSpread:[0,0]};
    constructor(reducedMotion=false) {
        this.reducedMotion=reducedMotion;this.time=0;this.age=0;
        this.name='idle';this.intensity=1;this.velocity={};
        this.current={x:0,y:0,roll:0,bow:0,pulse:0,flutter:0,headPitch:0,headYaw:0,
            armLift:0,armWave:0,mouth:0,weight:0,torso:0,headRoll:0,hairSway:0,
            wingSpread:0,blush:0,leftShoulder:0,leftElbow:0,leftWrist:0,
            rightShoulder:0,rightElbow:0,rightWrist:0};
    }
    setAction(action={}) {
        const requested=action.name||action.sprite_motion||'idle';
        const name=CharacterMotion.aliases[requested]||requested;
        this.name=Object.hasOwn(CharacterMotion.durations,name)?name:'idle';
        this.loop=!!action.loop;
        if(Number.isFinite(action.intensity)) this.intensity=Math.max(0,Math.min(1,action.intensity));
        this.age=0; // Preserve positions and velocities on interruption.
    }
    static ease(value) {const t=Math.max(0,Math.min(1,value));return t*t*(3-2*t);}
    sample(dt,mouth=0,speaking=false) {
        dt=Math.max(0,Math.min(Number.isFinite(dt)?dt:0,.2));this.time+=dt;this.age+=dt;
        const t=this.time,a=this.age,duration=this.loop?0:CharacterMotion.durations[this.name];
        const envelope=CharacterMotion.ease(a/.65)*(duration?1-CharacterMotion.ease((a-duration+1)):1);
        const target=Object.fromEntries(Object.keys(this.current).map(key=>[key,0]));
        Object.assign(target,{weight:envelope,headRoll:Math.sin(t*.65)*.008,
            headYaw:Math.sin(t*.4)*.008,hairSway:Math.sin(t*.65-.6)*.006,
            flutter:Math.sin(t*.8)*.003,leftShoulder:.015,rightShoulder:-.015,
            leftElbow:-.015,rightElbow:.015});
        switch(this.name) {
            case 'nod':target.headPitch=Math.sin(a*2.4)**2*.045*envelope;break;
            case 'think':target.headRoll+=.04*envelope;target.headPitch=-.012*envelope;break;
            case 'shy':target.headRoll-=.05*envelope;target.headPitch=.02*envelope;
                target.blush=.65*envelope;target.leftElbow-=.045*envelope;target.rightElbow+=.045*envelope;break;
            case 'laugh':target.headRoll+=Math.sin(a*2)*.012*envelope;
                target.headPitch=Math.sin(a*3)**2*.018*envelope;target.blush=.35*envelope;break;
            case 'present':target.leftShoulder+=.18*envelope;target.leftElbow-=.025*envelope;
                target.headRoll-=.018*envelope;target.armLift=.12*envelope;break;
        }
        // Voice has a fast envelope already; never pass lips through a slow body spring.
        this.current.mouth=speaking&&Number.isFinite(mouth)?Math.max(0,Math.min(1,mouth)):0;
        const omega=8,decay=Math.exp(-omega*dt);
        const strength=this.reducedMotion?0:Math.max(0,Math.min(1,Number.isFinite(this.intensity)?this.intensity:0));
        for(const key of Object.keys(this.current)) {
            if(key==='mouth')continue;
            const value=target[key]*(['weight','blush'].includes(key)?1:strength);
            const diff=this.current[key]-value,speed=this.velocity[key]||0;
            const change=(speed+omega*diff)*dt;
            let next=value+(diff+change)*decay;
            this.velocity[key]=(speed-omega*change)*decay;
            const limit=CharacterMotion.limits[key];
            if(limit) {
                const clamped=Math.max(limit[0],Math.min(limit[1],next));
                if(clamped!==next)this.velocity[key]=0;
                next=clamped;
            }
            this.current[key]=next;
        }
        return this.current;
    }
}
window.CharacterMotion=CharacterMotion;

