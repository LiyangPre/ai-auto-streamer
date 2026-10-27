// Vocal and accompaniment share one Web Audio clock. Only vocals drive lips.
class SingingPlayer {
    constructor({preview=false,muted=false,music,onStart=()=>{},onStop=()=>{},onStatus=()=>{}}={}) {
        Object.assign(this,{preview,muted,music,onStart,onStop,onStatus});this.sources=[];this.generation=0;
    }
    context() {
        if(!this.ctx){this.ctx=new (window.AudioContext||window.webkitAudioContext)();
            this.analyser=this.ctx.createAnalyser();this.analyser.fftSize=1024;this.analyser.smoothingTimeConstant=.15;
            this.waveform=new Float32Array(1024);this.spectrum=new Uint8Array(this.analyser.frequencyBinCount);
            this.analyser.connect(this.ctx.destination);}
        return this.ctx;
    }
    async load(performance) {
        if(!performance){this.stop();return;}
        if(this.performance?.id===performance.id)return;
        this.stop();this.performance=performance;const generation=this.generation;
        this.abort=new AbortController();
        const clock=performance.paused?performance.paused_at:Date.now()/1000;
        const remaining=performance.song.duration+Math.max(0,performance.vocal_delay)+performance.starts_at-clock;
        if(remaining<=0){this.stop();return;}
        this.expiry=setTimeout(()=>this.stop('expired'),(remaining+.5)*1000);
        this.paused=!!performance.paused;if(this.paused){this.pausedPosition=Math.max(0,performance.paused_at-performance.starts_at);clearTimeout(this.expiry);}
        if(this.preview){this.onStart(performance);return;}
        this.music?.setPerformance(performance.music);
        try {
            this.onStatus('准备演唱音频…');
            const ctx=this.context();
            const decode=async url=>{
                if(!/^\/api\/singing\/audio\/[a-f0-9]{32}(?:\/guide)?$/.test(url)&&!/^\/api\/stories\/audio\/[a-z0-9_-]+\/[a-f0-9]{32}$/.test(url)&&!/^\/audio\/tts_[a-z0-9_]+\.mp3$/.test(url)&&!/^\/media\/[a-f0-9]{32}\.mp3$/.test(url))throw Error('Invalid song source');
                const response=await fetch(url,{signal:this.abort.signal});
                if(!response.ok||Number(response.headers.get('Content-Length'))>20*1024*1024)throw Error('Audio unavailable');
                const bytes=await response.arrayBuffer();if(bytes.byteLength>20*1024*1024)throw Error('Audio too large');
                const buffer=await ctx.decodeAudioData(bytes);if(buffer.duration>601)throw Error('Audio too long');return buffer;
            };
            const buffers=await Promise.all([decode(performance.song.url),performance.backing_url?decode(performance.backing_url):null,
                performance.song.guide_url?decode(performance.song.guide_url):null]);
            if(generation!==this.generation)return;
            this.buffers=buffers;await this.unlock();
        } catch(error){if(generation===this.generation){this.stop('error');this.onStatus('表演音频加载失败，已返回聊天。');}}
    }
    async unlock() {
        if(!this.performance||this.preview||this.muted||this.paused||!this.buffers||this.started)return;
        const generation=this.generation;
        try {
            const ctx=this.context();await ctx.resume();
            if(generation!==this.generation||this.started)return;
            if(ctx.state!=='running'){this.onStatus('点击输出画面以允许演唱声音');return;}
            const p=this.performance,elapsed=Number.isFinite(this.resumeOffset)?this.resumeOffset:Math.max(0,Date.now()/1000-p.starts_at);this.resumeOffset=null;
            const when=ctx.currentTime+.08+Math.max(0,p.starts_at-Date.now()/1000);
            const vocalDelay=Math.max(0,p.vocal_delay),backingDelay=Math.max(0,-p.vocal_delay);
            if(this.buffers[2]&&!this.guideAnalyser){this.guideAnalyser=ctx.createAnalyser();this.guideAnalyser.fftSize=1024;
                this.guideMute=ctx.createGain();this.guideMute.gain.value=0;this.guideAnalyser.connect(this.guideMute);this.guideMute.connect(ctx.destination);}
            this.lipAnalyser=this.buffers[2]?this.guideAnalyser:this.analyser;
            if(elapsed>=vocalDelay+this.buffers[0].duration){this.stop('ended');return;}
            const schedule=(buffer,delay,volume,vocal,role=vocal?'vocal':'backing')=>{
                if(!buffer||elapsed>=delay+buffer.duration)return;
                const source=ctx.createBufferSource(),gain=ctx.createGain();source.buffer=buffer;gain.gain.value=volume;
                source.connect(gain);gain.connect(role==='guide'?this.guideAnalyser:vocal&&!this.buffers[2]?this.analyser:ctx.destination);
                const start=when+Math.max(0,delay-elapsed),offset=Math.max(0,elapsed-delay);
                this.sources.push({source,gain,role,volume});
                if(vocal){this.vocalWhen=start;this.vocalEnd=start+buffer.duration-offset;source.onended=()=>{if(generation===this.generation)this.stop('ended');};}
                source.start(start,offset);
            };
            schedule(this.buffers[0],vocalDelay,p.vocal_volume,true);
            schedule(this.buffers[1],backingDelay,p.backing_volume,false);
            schedule(this.buffers[2],vocalDelay,1,false,'guide');
            this.timelineWhen=when;this.timelineOffset=elapsed;this.started=true;this.onStatus('');this.onStart(p);
        } catch(error){if(generation===this.generation)this.onStatus('点击输出画面以允许演唱声音');}
    }
    get vocalPlaying(){return !!(this.started&&!this.muted&&this.performance?.vocal_volume>0&&this.ctx?.state==='running'&&this.ctx.currentTime>=this.vocalWhen&&this.ctx.currentTime<this.vocalEnd);}
    get position(){return this.paused?this.pausedPosition||0:this.started?this.timelineOffset+Math.max(0,this.ctx.currentTime-this.timelineWhen):this.performance?Math.max(0,Date.now()/1000-this.performance.starts_at):0;}
    pause(){if(!this.performance||this.paused)return;this.pausedPosition=this.position;this.paused=true;clearTimeout(this.expiry);this.stopSources();this.started=false;}
    resume(performance){if(!this.performance||this.performance.id!==performance.id){this.load(performance);return;}this.performance=performance;this.resumeOffset=this.pausedPosition;this.paused=false;clearTimeout(this.expiry);const remaining=performance.song.duration+Math.max(0,performance.vocal_delay)-this.position;this.expiry=setTimeout(()=>this.stop('expired'),Math.max(0,remaining+.5)*1000);if(this.preview)this.onStart(performance);else this.unlock();}
    stopSources(){for(const {source,gain} of this.sources){source.onended=null;try{source.stop();}catch{}source.disconnect();gain.disconnect();}this.sources=[];}
    setMuted(value){this.muted=value;if(this.started){for(const {gain,volume,role} of this.sources)gain.gain.value=role==='guide'?1:value?0:volume;}else if(!value)this.unlock();}
    stop(reason='stopped') {
        ++this.generation;clearTimeout(this.expiry);this.abort?.abort();
        this.stopSources();this.buffers=null;this.started=false;this.paused=false;
        const previous=this.performance;this.performance=null;
        this.music?.setPerformance(null);if(previous)this.onStop({reason,performance:previous});
    }
    dispose(){this.stop();const context=this.ctx;this.ctx=null;this.guideAnalyser=null;this.guideMute=null;this.lipAnalyser=null;this.analyser=null;context?.close();}
}
window.SingingPlayer=SingingPlayer;
