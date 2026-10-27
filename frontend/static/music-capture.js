// Local audio-only WebRTC. No STUN/TURN service, recording, or server transcoding.
class MusicBridge {
    constructor({stream=null,onStream=()=>{},onStatus=()=>{},onFatal=()=>{}}={}) {
        this.stream=stream;this.onStream=onStream;this.onStatus=onStatus;this.onFatal=onFatal;
        this.role=stream?'source':'output';this.peers=new Map();this.running=false;
    }
    start() {if(!this.running){this.running=true;this.connect();}}
    connect() {
        if(!this.running)return;
        const socket=this.socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/ws/music?role=${this.role}`);
        let messages=Promise.resolve();
        socket.onmessage=event=>{
            messages=messages.then(()=>{
                if(this.socket===socket&&this.running)return this.message(JSON.parse(event.data));
            }).catch(()=>{if(this.socket===socket)this.retry();});
        };
        socket.onclose=()=>{if(this.socket===socket&&this.running)this.retry();};
        socket.onerror=()=>socket.close();
        this.onStatus('connecting');
    }
    send(value){if(this.socket?.readyState===1)this.socket.send(JSON.stringify(value));}
    peer(id) {
        this.remove(id);
        const pc=new RTCPeerConnection({iceServers:[]});this.peers.set(id,pc);
        pc.onconnectionstatechange=()=>{
            if(this.peers.get(id)!==pc)return;
            if(['failed','disconnected'].includes(pc.connectionState)) {
                // Rejoin signaling to obtain a fresh offer; never leave a dead player connected.
                clearTimeout(pc.recovery);pc.recovery=setTimeout(()=>{
                    if(this.peers.get(id)===pc&&pc.connectionState!=='connected')this.retry();
                },pc.connectionState==='failed'?100:3500);
            }
            const connected=[...this.peers.values()].filter(p=>p.connectionState==='connected').length;
            this.onStatus(connected?'connected':'waiting',connected);
        };
        return pc;
    }
    async local(pc,description) {
        await pc.setLocalDescription(description);
        if(pc.iceGatheringState!=='complete')await new Promise(resolve=>{
            const done=()=>{clearTimeout(timer);pc.removeEventListener('icegatheringstatechange',change);pc.removeEventListener('signalingstatechange',change);resolve();};
            const change=()=>{if(pc.iceGatheringState==='complete'||pc.signalingState==='closed')done();};
            const timer=setTimeout(done,2500);
            pc.addEventListener('icegatheringstatechange',change);pc.addEventListener('signalingstatechange',change);
        });
        return pc.localDescription?.sdp;
    }
    async message(data) {
        if(data.type==='error'){this.stop();this.onFatal('另一个控制台正在采集，或输出窗口过多。请先停止那里的采集。');return;}
        if(data.type==='welcome'){this.onStatus('waiting');return;}
        if(this.role==='source'&&data.type==='receiver') {
            const pc=this.peer(data.id);
            for(const track of this.stream.getAudioTracks())pc.addTrack(track,this.stream);
            const sdp=await this.local(pc,await pc.createOffer());
            if(this.peers.get(data.id)===pc&&sdp)this.send({type:'offer',id:data.id,sdp});
        } else if(this.role==='source'&&data.type==='answer') {
            const pc=this.peers.get(data.id);
            if(pc?.signalingState==='have-local-offer')await pc.setRemoteDescription({type:'answer',sdp:data.sdp});
        } else if(this.role==='source'&&data.type==='receiver-left') {
            this.remove(data.id);
            this.onStatus('waiting',[...this.peers.values()].filter(pc=>pc.connectionState==='connected').length);
        } else if(this.role==='output'&&data.type==='offer') {
            const pc=this.peer('source');
            pc.ontrack=event=>{if(this.peers.get('source')===pc&&event.track.kind==='audio')this.onStream(new MediaStream([event.track]));};
            await pc.setRemoteDescription({type:'offer',sdp:data.sdp});
            const sdp=await this.local(pc,await pc.createAnswer());
            if(this.peers.get('source')===pc&&sdp)this.send({type:'answer',sdp});
        } else if(data.type==='source-off') {
            this.remove('source');this.onStream(null);this.onStatus('waiting');
        }
    }
    remove(id){const pc=this.peers.get(id);if(pc){this.peers.delete(id);clearTimeout(pc.recovery);pc.close();}}
    disconnect() {
        const socket=this.socket;this.socket=null;socket?.close();
        for(const id of this.peers.keys())this.remove(id);
        if(this.role==='output')this.onStream(null);
    }
    retry(){if(!this.running)return;this.disconnect();clearTimeout(this.retryTimer);this.onStatus('reconnecting');this.retryTimer=setTimeout(()=>this.connect(),2000);}
    stop(){this.running=false;clearTimeout(this.retryTimer);this.disconnect();}
}

class MusicCaptureControls {
    constructor(studio) {
        this.studio=studio;this.$=studio.$;this.generation=0;
        this.$('music-capture-start').addEventListener('click',()=>this.choose());
        for(const id of ['music-capture-stop','music-capture-stop-global'])this.$(id).addEventListener('click',()=>this.stop());
        window.addEventListener('pagehide',()=>this.stop());
        this.status('未采集 · 选择音乐标签页或支持独立音频的应用窗口。');
    }
    status(text) {
        this.$('music-capture-status').textContent=text;
        this.$('music-capture-global-status').textContent=text;
        this.$('music-capture-live').hidden=!this.stream;
        this.$('music-capture-stop').disabled=!this.stream;
    }
    update() {
        const system=this.$('scene-music-source').value==='system';
        this.$('music-file-controls').hidden=system;this.$('music-system-controls').hidden=!system;
        this.$('scene-music-preview').hidden=system;
        if(system)this.$('scene-music-preview').pause();
    }
    async choose() {
        if(!navigator.mediaDevices?.getDisplayMedia||!window.RTCPeerConnection) {
            this.status('此浏览器不支持采集。请在最新版 Chrome / Edge 打开本控制台，再选择音乐标签页。');return;
        }
        this.stop();const generation=++this.generation;
        this.$('music-capture-start').disabled=true;
        let stream;
        try {
            // Only the audio track is sent. Video capture is required by the browser picker.
            stream=await navigator.mediaDevices.getDisplayMedia({
                video:{displaySurface:'browser',frameRate:1,width:320,height:180},
                audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false,suppressLocalAudioPlayback:true},
                selfBrowserSurface:'exclude',monitorTypeSurfaces:'exclude',systemAudio:'exclude',
                windowAudio:'window',surfaceSwitching:'exclude'
            });
            if(generation!==this.generation){stream.getTracks().forEach(t=>t.stop());return;}
            if(!stream.getAudioTracks().length)throw new Error('没有收到音频。请选择音乐标签页并勾选「分享标签页音频」；应用窗口需要浏览器支持独立窗口音频。');
            if(stream.getVideoTracks().some(track=>track.getSettings().displaySurface==='monitor'))throw new Error('请选择独立音乐标签页/应用窗口，不支持整个系统混音（会把 Astra 的声音再次送入直播）。');
            this.stream=stream;
            for(const track of stream.getTracks())track.addEventListener('ended',()=>{if(this.stream===stream)this.stop();},{once:true});
            // Keep the permission session alive at minimal capture cost; never render/transmit video.
            for(const track of stream.getVideoTracks())track.enabled=false;
            this.$('scene-music-source').value='system';this.$('scene-music-enabled').checked=true;
            this.studio.dirty=true;this.studio.outputs();this.studio.previewScene();
            this.studio.status('Capture ready · 点击 Apply & save 将采集音频用于直播');
            this.bridge=new MusicBridge({stream:new MediaStream(stream.getAudioTracks()),
                onStatus:(state,count=0)=>this.status(count?`正在采集 · 已连接 ${count} 个输出（预览静音）`:
                    state==='reconnecting'?'连接中断，正在重连…':'正在采集 · 等待输出。Apply & save 后保持控制台打开。'),
                onFatal:message=>{this.stop();this.status(message);}});
            this.bridge.start();
        } catch(error) {
            stream?.getTracks().forEach(track=>track.stop());
            if(generation===this.generation){this.stop();this.status(['NotAllowedError','AbortError'].includes(error.name)?'已取消或未授权 · 可以重新选择音乐来源。':error.name==='NotReadableError'?'无法读取来源，请选择其他音乐标签页。':error.message||'采集失败，请用 Chrome / Edge 重试。');}
        } finally {this.$('music-capture-start').disabled=false;}
    }
    stop() {
        ++this.generation;this.bridge?.stop();this.bridge=null;
        const stream=this.stream;this.stream=null;stream?.getTracks().forEach(track=>track.stop());
        this.status('采集已停止 · 再次点击 Choose tab / app 才会继续；不会自动恢复授权。');
    }
}
window.MusicBridge=MusicBridge;
window.MusicCaptureControls=MusicCaptureControls;
