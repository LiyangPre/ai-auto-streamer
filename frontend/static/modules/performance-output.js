// Shared finite performance, welcome interlude and exact local audio-position resume.
window.createPerformanceAdapter=async function(client,moduleId){
    await StudioModules.script('/static/singing-player.js');
    let pendingWelcome=null,textTimer;
    const post=(action,body)=>fetch('/api/modules/'+moduleId+'/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Studio-API':'1'},body:JSON.stringify(body)}).catch(()=>{});
    const restoreChat=()=>{
        const look=client.modules?.presentation('chat')||{},expression=look.expression&&look.expression!=='auto'?look.expression:'neutral';
        client.carScene?.setSinging(false);client.carScene?.setExpression?.(expression==='neutral'?'normal':expression);
        client.renderer?.setAction({name:look.pose&&look.pose!=='auto'?look.pose:'idle',loop:true,intensity:look.motion_strength??.35});
        client.renderer?.face?.setExpression(expression,true);client.music?.setSpeaking(false);client.subtitles?.finish();client.voiceEnvelope?.reset();
    };
    const main=new SingingPlayer({preview:client.isPreview,muted:client.muted,music:client.music,
        onStart:p=>{const look=p.presentation||{pose:'idle',expression:moduleId==='singing'?'singing':'neutral',motion_strength:.25};
            client.carScene?.setSinging(true,look.expression==='neutral'?'normal':look.expression);
            client.renderer?.setAction({name:look.pose==='auto'?'idle':look.pose,loop:true,intensity:look.motion_strength});
            client.renderer?.face?.setExpression(look.expression==='auto'?'neutral':look.expression,true);
            client.subtitles?.begin();client.subtitles?.show((moduleId==='singing'?'♫ ':'☾ ')+p.song.title);},
        onStop:event=>{restoreChat();if(!client.isPreview&&['ended','error','expired'].includes(event?.reason))post('complete',{playback_id:event.performance.id});},
        onStatus:text=>{if(text){client.subtitles?.begin();client.subtitles?.show(text);}}
    });
    const resumeWelcome=()=>{if(!pendingWelcome||client.isPreview)return;post('resume-welcome',{playback_id:pendingWelcome.playback_id,welcome_id:pendingWelcome.greeting.id});};
    const welcome=new SingingPlayer({preview:client.isPreview,muted:client.muted,
        onStart:p=>{client.carScene?.setExpression('happy');client.renderer?.face?.setExpression('happy',true);client.subtitles?.show(p.song.title);},
        onStop:event=>{if(['ended','error','expired'].includes(event?.reason))resumeWelcome();}
    });
    const welcomeStart=event=>{
        if(!main.performance||event.playback_id!==main.performance.id)return;
        main.pause();welcome.stop();clearTimeout(textTimer);pendingWelcome=event;
        const greeting=event.greeting;
        if(!greeting.audio_url){client.subtitles?.show(greeting.text);textTimer=setTimeout(resumeWelcome,2000);return;}
        welcome.load({id:greeting.id,song:{url:greeting.audio_url,duration:greeting.duration,title:greeting.text},starts_at:greeting.starts_at||Date.now()/1000+.15,
            vocal_delay:0,vocal_volume:main.performance.vocal_volume,music:'shared',backing_url:null,backing_volume:0,bpm:60});
    };
    return {
        get active(){return !!main.performance;},
        get voice(){const player=welcome.performance?welcome:main;return {playing:player.vocalPlaying,ctx:player.ctx,analyser:player.lipAnalyser||player.analyser,waveform:player.waveform,spectrum:player.spectrum};},
        handle(event){
            if(event.type===moduleId+'_start'){client.stopAudio();main.load(event.performance);return true;}
            if(event.type===moduleId+'_stop'){pendingWelcome=null;clearTimeout(textTimer);welcome.stop();main.stop();return true;}
            if(event.type==='performance_welcome'&&event.module_id===moduleId){welcomeStart(event);return true;}
            if(event.type==='performance_resume'&&event.module_id===moduleId){pendingWelcome=null;clearTimeout(textTimer);welcome.stop();main.resume(event.performance);return true;}
            if(event.module_snapshots&&Object.hasOwn(event.module_snapshots,moduleId)){
                const value=event.module_snapshots[moduleId];if(value)client.stopAudio();main.load(value);
                if(value?.paused&&value.welcome)welcomeStart({playback_id:value.id,greeting:value.welcome});
            }return false;
        },
        frame(params){if(main.performance){const playing=welcome.performance?welcome.vocalPlaying:main.vocalPlaying;client.music?.setSpeaking(playing);
            if(moduleId==='singing'&&!main.paused)params.moduleSway={amount:main.performance.presentation?.motion_strength??.35,phase:main.position*main.performance.bpm/60*Math.PI};}},
        unlock(){main.unlock();welcome.unlock();},setMuted(value){main.setMuted(value);welcome.setMuted(value);},
        dispose(){clearTimeout(textTimer);main.dispose();welcome.dispose();}
    };
};
