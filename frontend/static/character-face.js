// Facial timing is independent of body motion and voice synthesis. No model calls.
class CharacterFace {
    static expressions={neutral:{smile:.22,squint:0,blush:.12,gaze:0},
        happy:{smile:1,squint:.38,blush:.6,gaze:0},
        singing:{smile:.7,squint:.5,blush:.4,gaze:0},
        shy:{smile:.6,squint:.18,blush:1,gaze:-.3},
        thinking:{smile:.05,squint:.12,blush:.1,gaze:.45},
        surprised:{smile:.12,squint:-.1,blush:.25,gaze:0}};
    constructor(random=Math.random) {
        this.random=random;this.time=0;this.nextBlink=2+random()*2;
        this.blinkAge=-1;this.name='neutral';this.hold=false;this.age=0;
        this.current={...CharacterFace.expressions.neutral};
    }
    setExpression(name,hold=false) {
        this.name=Object.hasOwn(CharacterFace.expressions,name)?name:'neutral';
        this.hold=hold;this.age=0;
    }
    setAction(action={}) {
        const names={shy:'shy',heart:'shy',peek:'shy',laugh:'happy',present:'happy',
            wave:'happy',think:'thinking',cry:'thinking',angry:'thinking',surprise:'surprised'};
        const emotions={normal:'neutral',happy:'happy',sad:'thinking',angry:'thinking',thinking:'thinking',surprised:'surprised'};
        this.setExpression(names[action.name]||emotions[action.emotion]||'neutral',!!action.loop);
    }
    blink() {if(this.blinkAge<0)this.blinkAge=0;}
    sample(dt) {
        dt=Math.max(0,Math.min(Number.isFinite(dt)?dt:0,.2));this.time+=dt;this.age+=dt;
        if(this.time>=this.nextBlink&&this.blinkAge<0)this.blink();
        let closure=0;
        if(this.blinkAge>=0) {
            this.blinkAge+=dt;
            const age=this.blinkAge;
            closure=age<.075?CharacterMotion.ease(age/.075):age<.115?1:1-CharacterMotion.ease((age-.115)/.13);
            if(age>=.245){this.blinkAge=-1;this.nextBlink=this.time+2.6+this.random()*3.4;closure=0;}
        }
        const target=CharacterFace.expressions[this.hold||this.age<4.5?this.name:'neutral'];
        for(const key of Object.keys(this.current))this.current[key]+=(target[key]-this.current[key])*(1-Math.exp(-dt*7));
        return {...this.current,blink:closure,eyeOpen:(1-closure)*(1-this.current.squint)};
    }
}

// RMS controls the jaw; spectral balance changes width/roundness.
// This approximates syllables; it does not claim phoneme recognition.
class VoiceEnvelope {
    constructor(){this.reset();}
    reset(){this.open=0;this.round=0;this.peak=.065;this.gate=false;}
    sample(samples,spectrum,sampleRate,dt,playing) {
        dt=Math.max(0,Math.min(Number.isFinite(dt)?dt:0,.2));
        let rms=0;
        if(playing&&samples?.length) {
            let mean=0;for(const n of samples)mean+=n;mean/=samples.length;
            for(const n of samples)rms+=(n-mean)**2;
            rms=Math.sqrt(rms/samples.length);
        }
        if(!Number.isFinite(rms))rms=0;
        this.gate=playing&&rms>(this.gate?.006:.010);
        this.peak=Math.max(.045,rms,this.peak*Math.exp(-dt*.65));
        const target=this.gate?Math.min(.95,Math.pow(Math.max(0,rms-.006)/this.peak,.72)*.88):0;
        const tau=target>this.open?.028:.055;
        this.open+=(target-this.open)*(1-Math.exp(-dt/tau));
        if(!playing)this.open=0;
        if(this.open<.012)this.open=0;
        let low=0,high=0;
        if(this.gate&&spectrum?.length&&sampleRate>0) {
            const binHz=sampleRate/(spectrum.length*2);
            for(let i=1;i<spectrum.length;i++) {
                const hz=i*binHz;
                if(hz>=150&&hz<900)low+=spectrum[i];
                else if(hz>=900&&hz<3200)high+=spectrum[i];
            }
        }
        const round=low+high>0?Math.max(0,Math.min(1,(low/(low+high)-.22)*2)):0;
        this.round+=(round-this.round)*(1-Math.exp(-dt*15));
        return {open:this.open,round:this.round,rms};
    }
}
window.CharacterFace=CharacterFace;
window.VoiceEnvelope=VoiceEnvelope;
