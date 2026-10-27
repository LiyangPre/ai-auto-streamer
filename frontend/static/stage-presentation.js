// Subtitle lifetime follows queued speech; music never enters the mouth analyser.
class StageSubtitles {
    constructor(element, seconds=4) {
        this.element=element;this.seconds=seconds;this.streaming=false;this.audioBusy=false;
    }
    cancel(){clearTimeout(this.timer);this.timer=null;}
    begin(){this.cancel();this.streaming=true;this.element.classList.remove('empty');}
    waitForSpeech(){this.cancel();this.streaming=true;if(!this.audioBusy)this.element.classList.add('empty');}
    show(text){this.cancel();this.element.textContent=text;this.element.classList.remove('thinking');this.element.classList.remove('empty');}
    finish(){this.streaming=false;this.schedule();}
    setAudioBusy(value){this.audioBusy=value;this.cancel();if(!value)this.schedule();}
    setDuration(seconds){this.seconds=seconds;this.cancel();this.schedule();}
    schedule() {
        this.cancel();
        if(this.streaming||this.audioBusy||this.element.classList.contains('empty'))return;
        this.timer=setTimeout(()=>{
            this.element.classList.add('empty');this.element.classList.remove('thinking');
            this.timer=null;
        },this.seconds*1000);
    }
    dispose(){this.cancel();}
}

class StageMusic {
    static source(settings) {
        return typeof settings.music_asset==='string'&&/^[a-f0-9]{32}$/.test(settings.music_asset)
            ? `/media/${settings.music_asset}.mp3` : '/static/music/conversation-lounge.mp3';
    }
    constructor({preview=false,muted=false}={}) {
        this.preview=preview;this.muted=muted;this.enabled=false;this.volume=.18;this.speaking=false;
    }
    configure(settings) {
        const source=StageMusic.source(settings);
        if(this.source!==source) {
            this.pause();this.source=source;
            if(this.audio){this.audio.src=source;this.audio.load?.();}
        }
        this.enabled=settings.music_enabled;this.volume=settings.music_volume;
        if(this.preview)return;
        if(!this.enabled||this.muted){this.pause();return;}
        this.start();this.fade();
    }
    start() {
        if(this.preview||this.muted||!this.enabled)return;
        if(!this.audio) {
            this.audio=new Audio(this.source || '/static/music/conversation-lounge.mp3');
            this.audio.loop=true;this.audio.preload='auto';this.audio.volume=0;
            this.audio.dataset.streamerMusic='loop';this.audio.hidden=true;
            document.body.appendChild(this.audio);
            this.audio.onplaying=()=>{this.audio.dataset.playback='playing';};
            this.audio.onerror=()=>{this.audio.dataset.playback='error';};
        }
        if(!this.audio.paused)return;
        this.audio.play().catch(()=>{this.audio.dataset.playback='blocked';});
    }
    unlock(){this.start();this.fade();}
    setMuted(value){this.muted=value;if(value)this.pause();else this.unlock();}
    setSpeaking(value){if(this.speaking===value)return;this.speaking=value;this.fade();}
    fade() {
        clearInterval(this.ramp);
        if(!this.audio||this.preview||this.muted||!this.enabled)return;
        const target=this.volume*(this.speaking?.25:1),from=this.audio.volume;
        const started=performance.now(),duration=this.speaking?220:1200;
        this.ramp=setInterval(()=>{
            const t=Math.min(1,(performance.now()-started)/duration),ease=t*t*(3-2*t);
            this.audio.volume=t===1?target:from+(target-from)*ease;
            if(t===1){clearInterval(this.ramp);this.ramp=null;}
        },40);
    }
    pause(){clearInterval(this.ramp);this.ramp=null;if(this.audio){this.audio.pause();this.audio.volume=0;this.audio.dataset.playback='paused';}}
    dispose(){this.pause();}
}
window.StageSubtitles=StageSubtitles;
window.StageMusic=StageMusic;
