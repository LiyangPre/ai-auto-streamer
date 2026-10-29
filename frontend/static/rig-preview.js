const POSES = {
    idle:['◡','待机 · Idle'],nod:['⌄','点头 · Nod'],think:['⋯','思考 · Think'],
    shy:['❀','害羞 · Shy'],laugh:['☺','轻笑 · Smile'],present:['↗','轻迎 · Welcome']
};
const canvas=document.querySelector('#rig-canvas'), context=canvas.getContext('2d');
const motion=new CharacterMotion(matchMedia('(prefers-reduced-motion: reduce)').matches);
const face=new CharacterFace(),voice=new VoiceEnvelope();
const expressionController=window.CharacterExpressionController?new window.CharacterExpressionController({
    profileId:'astra',reducedMotion:motion.reducedMotion,intensity:.55}):null;
const player=document.querySelector('#voice-player');
let audioContext,analyser,waveform,spectrum,portrait=false,voiceRequest=0;
let rig, last=performance.now(), touring=false, tourTime=0, tourIndex=0;
const tour=['idle','nod','think','shy','laugh','present'];
function choose(name, automatic=false) {
    if(!automatic) { touring=false; document.querySelector('#tour').setAttribute('aria-pressed','false'); }
    motion.setAction({name,loop:true,intensity:+document.querySelector('#strength').value});
    face.setAction({name,loop:true});document.querySelector('#expression').value=face.name;
    expressionController?.setExpression(face.name,{hold:true,source:'preview'});
    document.querySelector('#pose-name').textContent=POSES[name][1];
    document.querySelectorAll('[data-pose]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.pose===name)));
}
for(const [name,[icon,label]] of Object.entries(POSES)) {
    const button=document.createElement('button'); button.dataset.pose=name; button.type='button';
    const mark=document.createElement('b'); mark.textContent=icon; button.append(mark,document.createTextNode(label));
    button.setAttribute('aria-pressed',String(name==='idle')); button.addEventListener('click',()=>choose(name));
    document.querySelector('#poses').append(button);
}
document.querySelector('#tour').addEventListener('click',()=>{
    touring=!touring; tourTime=0; document.querySelector('#tour').setAttribute('aria-pressed',String(touring));
    if(touring) choose(tour[tourIndex++%tour.length],true);
});
document.querySelector('#speed').addEventListener('input',event=>{document.querySelector('#speed-value').textContent=event.target.value+'×';});
document.querySelector('#strength').addEventListener('input',event=>{
    motion.intensity=+event.target.value; document.querySelector('#strength-value').textContent=Math.round(motion.intensity*100)+'%';
});
document.querySelector('#expression').addEventListener('change',event=>{
    face.setExpression(event.target.value,true);expressionController?.setExpression(event.target.value,{hold:true,source:'preview'});
});
document.querySelector('#blink').addEventListener('click',()=>{face.blink();expressionController?.blink();});
document.querySelector('#portrait').addEventListener('click',event=>{
    portrait=!portrait;event.currentTarget.setAttribute('aria-pressed',String(portrait));
});
function connectVoice() {
    if(!audioContext) {
        audioContext=new (window.AudioContext||window.webkitAudioContext)();
        analyser=audioContext.createAnalyser();analyser.fftSize=1024;analyser.smoothingTimeConstant=.15;
        waveform=new Float32Array(analyser.fftSize);spectrum=new Uint8Array(analyser.frequencyBinCount);
        audioContext.createMediaElementSource(player).connect(analyser);analyser.connect(audioContext.destination);
    }
    audioContext.resume().catch(()=>{});
}
document.querySelector('#voice-test').addEventListener('click',async event=>{
    const button=event.currentTarget,request=++voiceRequest;
    connectVoice();player.pause();voice.reset();button.disabled=true;
    document.querySelector('#voice-status').textContent='Preparing the current voice…';
    try {
        const response=await fetch('/api/studio', {headers:{'X-Studio-API':'1'}});if(!response.ok)throw new Error();
        const data=await response.json();
        const preview=await fetch('/api/voice-preview',{method:'POST',headers:{'Content-Type':'application/json','X-Studio-API':'1'},
            body:JSON.stringify({voice:data.settings.voice,text:'晚上好，今天的小故事，你想听哪一段？我们慢慢聊，好不好？'})});
        if(!preview.ok)throw new Error();
        const result=await preview.json();
        if(request!==voiceRequest)return;
        player.src=result.audio_url;await player.play();
        document.querySelector('#voice-status').textContent='Playing the current voice · watch the lips and pauses.';
    } catch {if(request===voiceRequest)document.querySelector('#voice-status').textContent='Voice could not play. Retry, or press play on the audio controls.';}
    finally {if(request===voiceRequest)button.disabled=false;}
});
document.querySelector('#voice-stop').addEventListener('click',()=>{
    voiceRequest++;player.pause();voice.reset();document.querySelector('#voice-test').disabled=false;
    document.querySelector('#voice-status').textContent='Stopped · mouth closed.';
});
player.addEventListener('play',connectVoice);
player.addEventListener('ended',()=>{voice.reset();document.querySelector('#voice-status').textContent='Finished · mouth closed.';});
addEventListener('pagehide',()=>{voiceRequest++;player.pause();expressionController?.dispose();audioContext?.close();});
function resize(){ const box=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,1.5); canvas.width=Math.round(box.width*dpr);canvas.height=Math.round(box.height*dpr);context.setTransform(dpr,0,0,dpr,0,0); }
addEventListener('resize',resize);
CutoutRig.load('/static/avatar_dragon/rig.json').then(value=>{
    rig=value; resize(); document.querySelector('#loading').hidden=true; motion.intensity=+document.querySelector('#strength').value;
}).catch(()=>{document.querySelector('#loading').textContent='Rig assets could not load. Refresh to retry.';});
function frame(now) {
    requestAnimationFrame(frame);
    if(now-last<(document.hidden?200:1000/30)) return;
    const realDt=Math.min(.2,(now-last)/1000),dt=realDt*+document.querySelector('#speed').value;last=now;
    if(!rig) return;
    if(touring) {tourTime+=dt;if(tourTime>=4.6){tourTime=0;choose(tour[tourIndex++%tour.length],true);}}
    const playing=!player.paused&&!player.ended&&!player.muted&&player.volume>0&&player.readyState>=3&&audioContext?.state==='running';
    if(playing){analyser.getFloatTimeDomainData(waveform);analyser.getByteFrequencyData(spectrum);}
    const speech=voice.sample(waveform,spectrum,audioContext?.sampleRate||48000,realDt,!!playing);
    expressionController?.setSpeaking(!!playing);
    const pose=motion.sample(dt,speech.open,playing),expression=expressionController?
        expressionController.sample(realDt,{mouthOpen:speech.open,mouthRound:speech.round}):face.sample(realDt),box=canvas.getBoundingClientRect();
    document.querySelector('#lip-level').value=speech.open;
    document.querySelector('#lip-value').textContent=Math.round(speech.open*100)+'%';
    document.querySelector('#blink-value').textContent=expression.blink>.7?'Blinking':'Eyes open';
    context.clearRect(0,0,box.width,box.height);rig.draw(context,box.width,box.height,pose,pose.mouth,motion.reducedMotion,expression,speech.round,portrait);
}
requestAnimationFrame(frame);
