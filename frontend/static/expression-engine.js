/*! Local expression adapter. Built with `npm run build:expressions`.
 * Uses @soullink-emotion/engine 0.1.0-beta.1 (MIT); see vendor/licenses/.
 * The SDK generates FACS timelines. This boundary enforces our character
 * constraints; it never generates audio, fetches a model, or plans via an API.
 */
import {SoullinkRuntime, motionStylePresets, getVADPreset} from '@soullink-emotion/engine';

const SDK_VERSION = '0.1.0-beta.1';
const finite = (value, fallback=0) => typeof value === 'number' && Number.isFinite(value) ? value : fallback;
const bound = (value, low, high, fallback=0) => Math.max(low, Math.min(high, finite(value, fallback)));
const NEUTRAL = Object.freeze({smile:.22, squint:0, blush:.04, gaze:0, eyeOpen:1, headYaw:0, headRoll:0});
const ALIASES = Object.freeze({normal:'neutral', calm:'neutral', smile:'happy', laugh:'happy',
    think:'thinking', shy:'shy', smug:'shy', surprise:'surprised', sing:'singing'});
const NAMES = new Set(['neutral','happy','sad','angry','surprised','thinking','shy','singing']);
const LOCAL = Object.freeze({
    neutral:NEUTRAL, happy:{smile:.85,squint:.2,blush:.42,eyeOpen:.9,gaze:0},
    shy:{smile:.48,squint:.12,blush:.8,eyeOpen:.9,gaze:-.08},
    sad:{smile:-.15,squint:.03,blush:.04,eyeOpen:.82,gaze:0},
    angry:{smile:-.12,squint:.12,blush:.18,eyeOpen:.86,gaze:0},
    thinking:{smile:.08,squint:.06,blush:.04,eyeOpen:.93,gaze:.08},
    surprised:{smile:.25,squint:0,blush:.2,eyeOpen:1,gaze:0},
    singing:{smile:.7,squint:.22,blush:.4,eyeOpen:.86,gaze:0}
});

function normalizeName(value) {
    if (typeof value !== 'string') return 'neutral';
    const name = value.toLowerCase().trim().replace(/^\[([a-z]+)\]$/, '$1');
    const mapped = ALIASES[name] || name;
    return NAMES.has(mapped) ? mapped : 'neutral';
}

function virtualProfile(id) {
    // A semantic profile, not a pretend Live2D asset. Existing Canvas renderers
    // consume snapshot.facs through the safe canonical output below.
    const keys = ['eyeOpen','eyeSmile','eyeSquint','eyeBlinkL','eyeBlinkR','mouthSmile',
        'mouthFrown','gazeX','headX','headZ','blush','browInnerUp','browOuterUp','browDown','breath'];
    return {modelId:id, displayName:id, version:'1', modelPath:'',
        parameterMap:Object.fromEntries(keys.map(key => [key,{target:'Expression_'+key}])),
        idleConfig:{headX:[-.025,.025],headY:[-.015,.015],headZ:[-.015,.015],
            gazeX:[-.08,.08],gazeY:[-.04,.04],eyeOpen:[.97,1],breath:[.35,.55]}};
}

function profileId(value) {
    return typeof value === 'string' && /^[a-zA-Z0-9_-]{1,48}$/.test(value) ? value : 'astra';
}

class CharacterExpressionController {
    static sdkVersion = SDK_VERSION;
    static normalizeName = normalizeName;

    constructor(options={}) {
        options = options && typeof options === 'object' ? options : {};
        this.profileId = profileId(options.profileId);
        this.baseIntensity = bound(options.intensity,0,1,.55);
        this.intensity = this.baseIntensity;
        this.reducedMotion = options.reducedMotion === true;
        this.seed = Math.floor(bound(options.seed,1,2147483646,Math.floor(Math.random()*2147483645)+1));
        this.randomState = this.seed;
        this.disposed = false;
        this.enabled = true;
        this.motionStrength = .35;
        this.moduleId = 'chat';
        this.moduleKey = '';
        this._initializeRuntime();
        this.reset();
        if (options.moduleId) this.setModule(options.moduleId,options.presentation || {});
    }

    _initializeRuntime() {
        try {
            this.runtime = new SoullinkRuntime({profile:virtualProfile(this.profileId),
                motionStyle:{...motionStylePresets.calm,seed:this.seed,spontaneity:0,
                    idleActionGain:0,gestureFrequency:0,speechAccentGain:0,
                    microMotionGain:this.reducedMotion?0:.18},
                personality:{expressiveness:.6,softness:.9,shyness:.45,gazeStability:.9},
                emotionPersonality:{ambientDriftStrength:0,emotionHoldSeconds:2.5,decayRate:.85}});
            this.runtime.setLipSyncEnabled(false);
            this.runtime.setBodyMotionGain(this.reducedMotion?0:.15);
            this.runtime.setProactiveRepeatEnabled(false);
            this.engine = 'soullink';
        } catch {
            this.runtime = null;
            this.engine = 'local-fallback';
        }
    }

    _runtimeCall(method,...args) {
        if (!this.runtime) return null;
        try { return this.runtime[method](...args); }
        catch {
            // A package/model mismatch must never break audio or the stream.
            this.runtime = null;
            this.engine = 'local-fallback';
            return null;
        }
    }

    _random() {
        this.randomState = (Math.imul(this.randomState,1664525)+1013904223) >>> 0;
        return this.randomState/4294967296;
    }

    setProfile(id) {
        const next = profileId(id);
        if (this.disposed || next === this.profileId) return false;
        this.profileId = next;
        this._initializeRuntime();
        this.reset();
        return true;
    }

    setExpression(value,options={}) {
        if (this.disposed) return false;
        options = options && typeof options === 'object' ? options : {};
        if (value === 'blink') { this.blink(); return true; }
        this.expression = this.enabled ? normalizeName(value) : 'neutral';
        this.source = typeof options.source === 'string' ? options.source.slice(0,32) : 'chat';
        this.intensity = bound(options.intensity,0,1,this.moduleIntensity ?? this.baseIntensity);
        this.hold = options.hold === true ? Infinity : typeof options.hold === 'number'
            ? bound(options.hold,0,30,4.5) : 4.5;
        this.age = 0;
        const emotion = this.expression === 'singing' ? 'happy' : this.expression;
        // No classifier, planner, prompt, embedding or provider is called here.
        this._runtimeCall('triggerIntent',{emotion,intensity:this.intensity,
            contextTags:['local-approved-expression'],variant:this.expression==='singing'?'gentle_smile':undefined},this.time);
        return true;
    }

    setSpeaking(value) {
        if (this.disposed) return;
        this.speaking = value === true;
        // SDK lip-sync remains disabled even while speech is active. The host's
        // measured audio envelope is the sole owner of the mouth opening.
        this._runtimeCall('setVoicePlaybackActive',this.speaking);
    }

    setModule(id,presentation={}) {
        if (this.disposed) return false;
        presentation = presentation && typeof presentation === 'object' ? presentation : {};
        const module = typeof id === 'string' && /^[a-z0-9_-]{1,48}$/.test(id) ? id : 'chat';
        const enabled = presentation.expression_enabled !== false;
        const intensity = bound(presentation.expression_strength,0,1,this.baseIntensity);
        const motion = bound(presentation.motion_strength,0,1,.35);
        const expression = typeof presentation.expression === 'string' ? presentation.expression : 'auto';
        const key = JSON.stringify([module,enabled,intensity,motion,expression]);
        if (key === this.moduleKey) return false;
        const changedModule = module !== this.moduleId;
        this.moduleId = module;
        this.moduleKey = key;
        this.enabled = enabled;
        this.moduleIntensity = intensity;
        this.motionStrength = motion;
        if (!enabled) this.setExpression('neutral',{intensity:0,hold:true,source:'module'});
        else if (expression !== 'auto') this.setExpression(expression,{intensity,hold:true,source:'module'});
        else if (changedModule) this.setExpression(module==='singing'?'singing':'neutral',{intensity,hold:module!=='chat',source:'module'});
        else this.intensity = intensity;
        return true;
    }

    blink() { if (!this.disposed && this.manualBlink == null) this.manualBlink = 0; }

    _blink(dt,sdkBlink=0) {
        // Preserve a complete paired cycle even if a new SDK reaction defers
        // its blink halfway through. SDK timing may start a cycle, but cannot
        // abruptly reopen either eye; the local clock also works in fallback.
        if (this.manualBlink == null && (this.time >= this.nextBlink
                || sdkBlink > .1 && this.time-this.lastBlink > 2)) this.manualBlink = 0;
        let manual = 0;
        if (this.manualBlink != null) {
            this.manualBlink += dt;
            const age = this.manualBlink;
            const smooth = x => { x=bound(x,0,1); return x*x*(3-2*x); };
            manual = age < .065 ? smooth(age/.065) : age < .1 ? 1 : 1-smooth((age-.1)/.13);
            if (age >= .23) {
                this.manualBlink = null;
                this.lastBlink = this.time;
                this.nextBlink = this.time+2.8+this._random()*3.2;
                manual = 0;
            }
        }
        return bound(manual,0,1);
    }

    _target(facs) {
        const gain = this.enabled ? this.intensity : 0;
        const active = this.expression !== 'neutral' && gain > 0;
        let target = {...NEUTRAL};
        if (facs && this.runtime) {
            if (active) {
                target.smile += (bound(facs.mouthSmile,0,1,.04)-.04)*.88*gain
                    - bound(facs.mouthFrown,0,1)*.5*gain;
                target.squint = (bound(facs.eyeSquint,0,1)*.3+bound(facs.eyeSmile,0,1)*.18)*gain;
                target.blush += bound(facs.blush,0,1)*.72*gain;
                target.eyeOpen = 1+(bound(facs.eyeOpen,.65,1,1)-1)*gain;
            }
            if (!this.reducedMotion) {
                target.gaze = bound(facs.gazeX,-1,1)*.35*this.motionStrength;
                target.headYaw = bound(facs.headX,-1,1)*2*this.motionStrength;
                target.headRoll = bound(facs.headZ,-1,1)*.025*this.motionStrength;
            }
        } else if (active) {
            for (const key of Object.keys(NEUTRAL)) {
                const value = LOCAL[this.expression][key] ?? NEUTRAL[key];
                target[key] += (value-NEUTRAL[key])*gain;
            }
        }
        target.smile = bound(target.smile,-.4,1,.22);
        target.squint = bound(target.squint,0,.5);
        target.blush = bound(target.blush,0,1,.04);
        target.eyeOpen = bound(target.eyeOpen*(1-target.squint),.45,1,1);
        if (this.reducedMotion) target.gaze=target.headYaw=target.headRoll=0;
        return target;
    }

    sample(delta,audio={}) {
        audio = audio && typeof audio === 'object' ? audio : {};
        const dt = bound(delta,0,.1);
        if (this.disposed) return this._output(0,{});
        this.time += dt;
        this.age += dt;
        if (this.expression !== 'neutral' && this.age > this.hold && !this.speaking) {
            this.setExpression('neutral',{intensity:0,source:'idle'});
        }
        if (this.hold === Infinity && this.expression !== 'neutral' && this.enabled)
            this._runtimeCall('applyVADTarget',getVADPreset(this.expression==='singing'?'happy':this.expression),Math.min(.08,dt*.7));
        const snapshot = this._runtimeCall('update',this.time,dt);
        const facs = snapshot?.facs;
        const target = this._target(facs);
        const blend = 1-Math.exp(-dt*7);
        for (const key of Object.keys(NEUTRAL)) this.current[key] += (target[key]-this.current[key])*blend;
        const closure = this._blink(dt,Math.max(bound(facs?.eyeBlinkL,0,1),bound(facs?.eyeBlinkR,0,1)));
        return this._output(closure,audio);
    }

    _output(blink,audio) {
        const eyeOpen = bound(this.current.eyeOpen*(1-blink),0,1,1);
        return {...this.current,blink,eyeOpen,eyeOpenL:eyeOpen,eyeOpenR:eyeOpen,
            enabled:this.enabled,intensity:this.enabled?this.intensity:0,
            // Head yaw is degrees; head roll is radians. No body/limb channels.
            mouthOpen:this.speaking?bound(audio.mouthOpen,0,1):0,
            mouthRound:this.speaking?bound(audio.mouthRound,0,1):0,
            expression:this.expression,emotion:this.expression,engine:this.engine,sdkVersion:SDK_VERSION};
    }

    reset() {
        if (this.disposed) return false;
        this.time = 0; this.age = 0; this.expression = 'neutral'; this.source = 'reset';
        this.speaking = false; this.hold = 4.5; this.intensity = this.baseIntensity;
        this.current = {...NEUTRAL}; this.manualBlink = null; this.lastBlink = 0;
        this.nextBlink = 2.8+this._random()*3.2;
        this.moduleKey = '';
        this._runtimeCall('reset',0);
        return true;
    }

    dispose() {
        this.reset(); this.runtime = null; this.disposed = true; this.engine = 'disposed';
    }
}

if (typeof window !== 'undefined') window.CharacterExpressionController = CharacterExpressionController;
export {CharacterExpressionController};
