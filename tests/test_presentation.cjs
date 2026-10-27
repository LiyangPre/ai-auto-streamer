const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {test}=require('node:test');
function fixture() {
    let time=0,id=0;const jobs=new Map(),audios=[];
    const add=(fn,ms,repeat)=>{const key=++id;jobs.set(key,{fn,at:time+ms,repeat});return key;};
    const advance=ms=>{
        const end=time+ms;
        while(true){const next=[...jobs].filter(([,j])=>j.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;
            const [key,j]=next;time=j.at;if(j.repeat)j.at+=j.repeat;else jobs.delete(key);j.fn();}
        time=end;
    };
    class Audio {
        constructor(src){this.src=src;this.dataset={};this.paused=true;this.volume=0;audios.push(this);}
        play(){this.paused=false;this.onplaying?.();return Promise.resolve();}
        pause(){this.paused=true;}
    }
    const bridges=[];
    class MusicBridge {
        constructor(options){this.options=options;bridges.push(this);}
        start(){this.running=true;}
        stop(){this.running=false;this.options.onStream(null);}
    }
    const sandbox={window:{MusicBridge,RTCPeerConnection:true},Audio,document:{body:{appendChild(){}}},performance:{now:()=>time},
        setTimeout:(f,m)=>add(f,m,0),clearTimeout:k=>jobs.delete(k),
        setInterval:(f,m)=>add(f,m,m),clearInterval:k=>jobs.delete(k)};
    vm.createContext(sandbox);vm.runInContext(fs.readFileSync('frontend/static/stage-presentation.js','utf8'),sandbox);
    const classes=new Set(['empty']);
    const element={textContent:'',classList:{add:k=>classes.add(k),remove:k=>classes.delete(k),contains:k=>classes.has(k)}};
    return {...sandbox.window,element,advance,audios,jobs,bridges};
}
test('subtitles wait for all queued speech, then expire after the configured hold',()=>{
    const f=fixture(),s=new f.StageSubtitles(f.element,4);
    s.begin();f.element.textContent='A longer spoken reply';s.setAudioBusy(true);s.finish();
    f.advance(20000);assert.equal(f.element.classList.contains('empty'),false);
    s.setAudioBusy(false);f.advance(3999);assert.equal(f.element.classList.contains('empty'),false);
    f.advance(1);assert.equal(f.element.classList.contains('empty'),true);
});
test('new replies cancel old hide timers; text-only replies and disposal are bounded',()=>{
    const f=fixture(),s=new f.StageSubtitles(f.element,2);
    s.begin();s.finish();f.advance(1000);s.begin();f.advance(2000);
    assert.equal(f.element.classList.contains('empty'),false);
    s.finish();f.advance(2000);assert.equal(f.element.classList.contains('empty'),true);
    s.begin();s.finish();s.dispose();assert.equal(f.jobs.size,0);
});
test('waiting for synthesis or buffered audio keeps subtitles hidden until playback',()=>{
    const f=fixture(),s=new f.StageSubtitles(f.element,2);
    f.element.textContent='Previous reply';s.waitForSpeech();f.advance(10000);
    assert.equal(f.element.classList.contains('empty'),true);
    s.setAudioBusy(true);s.finish();f.advance(10000);
    assert.equal(f.element.classList.contains('empty'),true,'a ready URL is not audible playback');
    s.show('Now speaking');s.finish();
    assert.equal(f.element.classList.contains('empty'),false);
    s.setAudioBusy(false);f.advance(2000);assert.equal(f.element.classList.contains('empty'),true);
});
test('music ducks only when speaking and returns smoothly, using a separate looping player',()=>{
    const f=fixture(),m=new f.StageMusic();m.configure({music_enabled:true,music_volume:.18});
    f.advance(1200);assert.equal(f.audios.length,1);assert.equal(m.audio.loop,true);
    assert.equal(m.audio.volume,.18);m.setSpeaking(true);f.advance(240);
    assert.equal(m.audio.volume,.045);m.setSpeaking(false);f.advance(600);
    assert.ok(m.audio.volume>.045&&m.audio.volume<.18);f.advance(600);assert.equal(m.audio.volume,.18);
    m.setMuted(true);assert.equal(m.audio.paused,true);assert.equal(m.audio.volume,0);assert.equal(f.jobs.size,0);
    m.setMuted(false);f.advance(1200);assert.equal(m.audio.paused,false);
    m.configure({music_enabled:false,music_volume:.18});assert.equal(m.audio.paused,true);assert.equal(f.jobs.size,0);
});
test('controller previews never create music players, even when preview voice is unmuted',()=>{
    const f=fixture(),m=new f.StageMusic({preview:true});m.configure({music_enabled:true,music_volume:.4});
    m.unlock();m.setSpeaking(true);assert.equal(f.audios.length,0);
});

test('live capture replaces file audio, ducks, stays shared, and stops on mute or disconnect',()=>{
    const f=fixture(),m=new f.StageMusic(),settings={music_source:'system',music_enabled:true,music_volume:.2};
    m.configure(settings);assert.equal(f.audios.length,0);assert.equal(f.bridges.length,1);
    const stream={id:'music-only'};f.bridges[0].options.onStream(stream);f.advance(1200);
    assert.equal(m.audio.srcObject,stream);assert.equal(m.audio.loop,false);assert.equal(m.audio.volume,.2);
    m.setSpeaking(true);f.advance(240);assert.equal(m.audio.volume,.05);
    m.configure(settings);assert.equal(f.bridges.length,1,'character changes preserve shared capture');
    f.bridges[0].options.onStream(null);assert.equal(m.audio.paused,true);
    f.bridges[0].options.onStream(stream);f.advance(240);assert.equal(m.audio.paused,false);
    m.setMuted(true);assert.equal(f.bridges[0].running,false);assert.equal(m.audio.srcObject,null);
    m.setMuted(false);assert.equal(f.bridges.length,2);
    m.configure({...settings,music_source:'file'});f.advance(240);
    assert.equal(f.bridges[1].running,false);assert.equal(m.audio.loop,true);assert.equal(m.audio.paused,false);
    assert.equal(m.audio.src,'/static/music/conversation-lounge.mp3');
    m.dispose();assert.equal(f.jobs.size,0);
});

test('preview and disabled music never subscribe to capture',()=>{
    const f=fixture(),settings={music_source:'system',music_enabled:true,music_volume:.2};
    const preview=new f.StageMusic({preview:true});preview.configure(settings);preview.unlock();preview.setMuted(false);
    const off=new f.StageMusic();off.configure({...settings,music_enabled:false});off.unlock();
    assert.equal(f.bridges.length,0);assert.equal(f.audios.length,0);
});

test('uploaded tracks loop and duck, change only when selected, and preserve playback across character changes',()=>{
    const f=fixture(),m=new f.StageMusic(),id='b'.repeat(32);
    m.configure({music_enabled:true,music_volume:.2,music_asset:id});
    f.advance(1200);assert.equal(m.audio.src,`/media/${id}.mp3`);assert.equal(m.audio.loop,true);
    m.audio.currentTime=12;m.setSpeaking(true);f.advance(240);assert.equal(m.audio.volume,.05);
    m.configure({music_enabled:true,music_volume:.2,music_asset:id});
    assert.equal(m.audio.currentTime,12);assert.equal(f.audios.length,1);
    let loads=0;m.audio.load=()=>{loads++;m.audio.currentTime=0;};
    m.configure({music_enabled:true,music_volume:.2,music_asset:'c'.repeat(32)});
    f.advance(240);assert.equal(loads,1);assert.equal(m.audio.volume,.05);
    assert.equal(m.audio.src,'/media/'+'c'.repeat(32)+'.mp3');
    m.configure({music_enabled:true,music_volume:.2,music_asset:'../../private.mp3'});
    assert.equal(m.audio.src,'/static/music/conversation-lounge.mp3');
});
