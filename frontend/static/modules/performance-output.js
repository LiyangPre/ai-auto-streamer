// Shared finite performance, welcome interlude and exact local audio-position resume.
window.createPerformanceAdapter=async function(client,moduleId){
    await StudioModules.script('/static/singing-player.js');
    let pendingWelcome=null,textTimer,pendingMainLook=null,activePlaybackId=null,lastStoppedId=null,lastIntroId=null,lastStoryCue=null,lastLyricKey=null,muted=!!client.muted;
    let lyricCues=[],lyricTitlePending=false;
    const outroPlayed=new Set();
    const storyEmotions=new Set(['neutral','happy','shy','thinking','surprised','sad']);
    const compileLyrics=song=>{
        const raw=Array.isArray(song?.lyrics_cues)?song.lyrics_cues:[];
        const sorted=raw.slice(0,2000).filter(cue=>cue&&Number.isFinite(cue.at)&&cue.at>=0&&cue.at<=3600&&
            typeof cue.text==='string'&&cue.text.length<=300).map(cue=>({at:cue.at,text:cue.text})).sort((a,b)=>a.at-b.at);
        lyricCues=[];
        for(const cue of sorted){
            const previous=lyricCues[lyricCues.length-1];
            if(previous&&previous.at===cue.at){
                if(cue.text&&!previous.text.split('\n').includes(cue.text)&&previous.text.split('\n').length<3)
                    previous.text=previous.text?previous.text+'\n'+cue.text:cue.text;
            }else lyricCues.push(cue);
        }
        lastLyricKey=null;
    };
    const lyricFrame=()=>{
        const p=main.performance;
        if(moduleId!=='singing'||!p||pendingWelcome||!main.timelinePlaying)return;
        if(!lyricCues.length){
            if(lyricTitlePending){client.subtitles?.show('♫ '+p.song.title);lyricTitlePending=false;}
            return;
        }
        const offset=Number.isFinite(p.song.lyrics_offset)?Math.max(-30,Math.min(30,p.song.lyrics_offset)):0;
        const position=main.position+Math.max(0,p.start_offset||0)-Math.max(0,p.vocal_delay||0)-offset;
        let low=0,high=lyricCues.length-1,index=-1;
        while(low<=high){const middle=(low+high)>>1;if(lyricCues[middle].at<=position){index=middle;low=middle+1;}else high=middle-1;}
        const key=p.id+'|'+index+'|'+offset;if(key===lastLyricKey)return;lastLyricKey=key;
        const text=index<0?'♫ '+p.song.title:lyricCues[index].text;
        if(text)client.subtitles?.show(text);
        else if(client.subtitles?.clear)client.subtitles.clear();
        else client.subtitles?.show('');
    };
    const storyFace=()=>{
        const p=main.performance,look=p?.presentation;
        if(moduleId!=='story'||!p||pendingWelcome||!main.timelinePlaying||look?.expression!=='auto'||look.expression_enabled===false)return;
        let cue=null;
        const cues=p.song?.expression_cues;
        for(const item of (Array.isArray(cues)?cues:[]).slice(0,12)){
            if(item&&typeof item==='object'&&Number.isFinite(item.at)&&item.at>=0&&item.at<=main.position&&storyEmotions.has(item.emotion)
                    &&(!cue||item.at>=cue.at))cue=item;
        }
        if(!cue)return;
        const key=p.id+'|'+cue.at+'|'+cue.emotion;if(key===lastStoryCue)return;lastStoryCue=key;
        client.expressionController?.setExpression(cue.emotion,{hold:true,source:'story_cue'});
        client.carScene?.setExpression(cue.emotion==='neutral'?'normal':cue.emotion);
        client.renderer?.face?.setExpression(cue.emotion,true);
    };
    const validClip=clip=>clip&&typeof clip.text==='string'&&clip.text.length>0&&
        typeof clip.audio_url==='string'&&/^\/audio\/(?:tts_[a-z0-9_]+|module_transition_[a-f0-9]{64})\.mp3$/.test(clip.audio_url)&&
        Number.isFinite(clip.duration)&&clip.duration>0&&clip.duration<=30;
    const post=(action,body)=>fetch('/api/modules/'+moduleId+'/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Studio-API':'1'},body:JSON.stringify(body)}).catch(()=>{});
    const restoreChat=()=>{
        const look=client.modules?.presentation('chat')||{},expression=look.expression&&look.expression!=='auto'?look.expression:'neutral';
        client.expressionController?.setModule('chat',look);
        client.expressionController?.setExpression(expression,{hold:true,source:'module_return'});
        client.expressionController?.setSpeaking(false);
        client.carScene?.setSinging(false);client.carScene?.setExpression?.(expression==='neutral'?'normal':expression);
        client.singingScene?.setSinging(false);client.singingScene?.setExpression?.(expression==='neutral'?'normal':expression);
        client.renderer?.setAction({name:look.pose&&look.pose!=='auto'?look.pose:'idle',loop:true,intensity:look.motion_strength??.35});
        client.renderer?.face?.setExpression(expression,true);client.music?.setSpeaking(false);client.subtitles?.finish();client.voiceEnvelope?.reset();
    };
    const showMain=p=>{const look=p.presentation||{pose:'idle',expression:moduleId==='singing'?'singing':'neutral',motion_strength:.25},
                expression=look.expression&&look.expression!=='auto'?look.expression:moduleId==='singing'?'singing':'neutral';
            client.expressionController?.setModule(moduleId,look);
            client.expressionController?.setExpression(expression,{hold:true,source:'module'});
            client.carScene?.setSinging(true,expression==='neutral'?'normal':expression);
            client.singingScene?.setSinging(moduleId==='singing',expression==='neutral'?'normal':expression);
            client.renderer?.setAction({name:look.pose==='auto'?'idle':look.pose,loop:true,intensity:look.motion_strength});
            client.renderer?.face?.setExpression(expression,true);
            client.subtitles?.begin();client.subtitles?.show((moduleId==='singing'?'♫ ':'☾ ')+p.song.title);};
    const main=new SingingPlayer({preview:client.isPreview,muted:client.muted,music:client.music,
        onStart:p=>{
            // SingingPlayer announces source scheduling before its audible
            // starts_at. Leave the intro running while the song preloads.
            if(!client.isPreview&&!muted&&validClip(p.intro)&&Date.now()/1000<p.starts_at){pendingMainLook=p.id;return;}
            if(!pendingWelcome)welcome.stop();pendingMainLook=null;showMain(p);
        },
        onStop:event=>{restoreChat();if(!client.isPreview&&['ended','error','expired'].includes(event?.reason))post('complete',{playback_id:event.performance.id,reason:event.reason});},
        onStatus:text=>{if(text){client.subtitles?.begin();client.subtitles?.show(text);}}
    });
    const resumeWelcome=()=>{if(!pendingWelcome||client.isPreview)return;post('resume-welcome',{playback_id:pendingWelcome.playback_id,welcome_id:pendingWelcome.greeting.id});};
    const welcome=new SingingPlayer({preview:client.isPreview,muted:client.muted,
        onStart:p=>{client.expressionController?.setExpression('happy',{hold:false,source:pendingWelcome?'welcome':'module_intro'});client.carScene?.setExpression('happy');client.renderer?.face?.setExpression('happy',true);client.subtitles?.show(p.song.title);},
        onStop:event=>{if(['ended','error','expired'].includes(event?.reason))resumeWelcome();}
    });
    const loadPerformance=p=>{
        if(p&&activePlaybackId!==p.id){pendingWelcome=null;pendingMainLook=null;lastStoryCue=null;clearTimeout(textTimer);welcome.stop();activePlaybackId=p.id;}
        compileLyrics(p?.song);lyricTitlePending=false;
        main.load(p);
        const intro=p?.intro,now=Date.now()/1000;
        if(!p||p.paused||client.isPreview||muted||!validClip(intro)||!Number.isFinite(intro.starts_at)||
                now>=intro.starts_at+intro.duration||now>=p.starts_at||lastIntroId===p.id)return;
        lastIntroId=p.id;
        welcome.load({id:p.id+'-intro',song:{url:intro.audio_url,duration:intro.duration,title:intro.text},starts_at:intro.starts_at,
            vocal_delay:0,vocal_volume:p.vocal_volume,music:'shared',backing_url:null,backing_volume:0,bpm:60});
    };
    const welcomeStart=event=>{
        if(!main.performance||event.playback_id!==main.performance.id)return;
        main.pause();welcome.stop();clearTimeout(textTimer);pendingWelcome=event;lastStoryCue=null;
        client.expressionController?.setSpeaking(false);client.voiceEnvelope?.reset();
        client.expressionController?.setExpression('happy',{hold:false,source:'welcome'});
        const greeting=event.greeting;
        if(!greeting.audio_url){client.subtitles?.show(greeting.text);textTimer=setTimeout(resumeWelcome,2000);return;}
        welcome.load({id:greeting.id,song:{url:greeting.audio_url,duration:greeting.duration,title:greeting.text},starts_at:greeting.starts_at||Date.now()/1000+.15,
            vocal_delay:0,vocal_volume:main.performance.vocal_volume,music:'shared',backing_url:null,backing_volume:0,bpm:60});
    };
    return {
        moduleId,
        get active(){return !!main.performance;},
        get voice(){const player=welcome.performance?welcome:main;return {playing:player.vocalPlaying,ctx:player.ctx,analyser:player.lipAnalyser||player.analyser,waveform:player.waveform,spectrum:player.spectrum};},
        handle(event){
            if(event.type===moduleId+'_start'){client.stopAudio();loadPerformance(event.performance);return true;}
            if(event.type==='singing_lyrics_updated'&&moduleId==='singing'){
                if(main.performance?.song?.id===event.song_id){
                    main.performance.song={...main.performance.song,lyrics_cues:event.lyrics_cues,lyrics_offset:event.lyrics_offset};
                    compileLyrics(main.performance.song);
                    lyricTitlePending=!lyricCues.length;
                    if(lyricTitlePending&&main.timelinePlaying&&!pendingWelcome){client.subtitles?.show('♫ '+main.performance.song.title);lyricTitlePending=false;}
                }
                return true;
            }
            if(event.type===moduleId+'_stop'){
                if(event.playback_id&&activePlaybackId&&event.playback_id!==activePlaybackId)return true;
                const stoppedId=event.playback_id||activePlaybackId||lastStoppedId||'';
                pendingWelcome=null;pendingMainLook=null;lastStoryCue=null;lastLyricKey=null;lyricCues=[];lyricTitlePending=false;clearTimeout(textTimer);welcome.stop();main.stop();restoreChat();
                lastStoppedId=stoppedId;activePlaybackId=null;
                const clip=event.transition,key=stoppedId+'|'+(clip?.id||clip?.audio_url||'')+'|'+(clip?.text||'');
                if(!client.isPreview&&!muted&&validClip(clip)&&!outroPlayed.has(key)){
                    outroPlayed.add(key);if(outroPlayed.size>64)outroPlayed.delete(outroPlayed.values().next().value);
                    client.playAudio?.(clip.audio_url,clip.text,'happy');
                }
                return true;
            }
            if(event.type==='performance_welcome'&&event.module_id===moduleId){welcomeStart(event);return true;}
            if(event.type==='performance_resume'&&event.module_id===moduleId){pendingWelcome=null;lastStoryCue=null;clearTimeout(textTimer);welcome.stop();
                const look=event.performance.presentation||main.performance?.presentation||{};
                client.expressionController?.setModule(moduleId,look);
                const expression=look.expression&&look.expression!=='auto'?look.expression:moduleId==='singing'?'singing':'neutral';
                client.expressionController?.setExpression(expression,{hold:true,source:'module_resume'});
                main.resume(event.performance);return true;}
            if(event.module_snapshots&&Object.hasOwn(event.module_snapshots,moduleId)){
                const value=event.module_snapshots[moduleId];if(value)client.stopAudio();loadPerformance(value);
                if(value?.paused&&value.welcome)welcomeStart({playback_id:value.id,greeting:value.welcome});
            }return false;
        },
        frame(params){if(main.performance){
            if(!pendingWelcome&&main.timelinePlaying){if(welcome.performance)welcome.stop();if(pendingMainLook===main.performance.id){showMain(main.performance);pendingMainLook=null;}}
            storyFace();
            lyricFrame();
            const playing=welcome.performance?welcome.vocalPlaying:main.vocalPlaying;client.music?.setSpeaking(playing);
            params.performanceTimeline={moduleId,playing:!pendingWelcome&&main.timelinePlaying,
                position:main.position,bpm:main.performance.bpm,motionStrength:main.performance.presentation?.motion_strength??.35};
            if(moduleId==='singing'&&main.timelinePlaying)params.moduleSway={amount:main.performance.presentation?.motion_strength??.35,phase:main.position*main.performance.bpm/60*Math.PI};}},
        unlock(){main.unlock();welcome.unlock();},setMuted(value){muted=!!value;main.setMuted(value);welcome.setMuted(value);if(value){client.expressionController?.setSpeaking(false);client.voiceEnvelope?.reset();}},
        dispose(){clearTimeout(textTimer);main.dispose();welcome.dispose();}
    };
};
