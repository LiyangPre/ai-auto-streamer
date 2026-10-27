class SharedMediaControls {
    constructor(studio) {
        this.studio=studio;this.$=studio.$;this.assets=[];
        for(const kind of ['background','music']) {
            const input=this.$(`media-${kind}-file`);
            this.$(`media-${kind}-upload`).addEventListener('click',()=>input.click());
            input.addEventListener('change',()=>{const file=input.files[0];if(file)this.upload(kind,file);input.value='';});
        }
        this.$('scene-music-track').addEventListener('change',()=>this.updatePlayer());
    }
    async load() {
        const data=await this.studio.request('/api/media');
        this.assets=data.assets;this.syncOptions();
    }
    syncOptions() {
        for(const kind of ['background','music']) {
            const select=this.$(kind==='background'?'scene-background-select':'scene-music-track');
            const previous=select.value;
            const group=this.$(`media-${kind}-options`);group.replaceChildren();
            for(const asset of this.assets.filter(item=>item.kind===kind)) {
                const option=document.createElement('option');
                option.value=kind==='background'?'media:'+asset.id:asset.id;
                const detail=kind==='background'?`${asset.width} × ${asset.height}`:`${Math.floor(asset.duration/60)}:${String(Math.floor(asset.duration%60)).padStart(2,'0')}`;
                option.textContent=`${asset.name} · ${detail}`;group.append(option);
            }
            if(previous)this.selectValue(select,previous,kind);
        }
        this.updatePlayer();
    }
    selectValue(select,value,kind) {
        if(![...select.options].some(option=>option.value===value)) {
            const option=document.createElement('option');option.value=value;
            option.textContent='Uploaded file · 本地素材（读取中或已移除）';
            this.$(`media-${kind}-options`).append(option);
        }
        select.value=value;
    }
    fill(scene) {
        this.selectValue(this.$('scene-background-select'),scene.background==='custom'?'media:'+scene.background_asset:scene.background,'background');
        this.selectValue(this.$('scene-music-track'),scene.music_asset||'','music');
        for(const [kind,id] of [['background',scene.background_asset],['music',scene.music_asset]]) {
            const asset=this.assets.find(item=>item.id===id);
            this.$(`media-${kind}-status`).textContent=asset?`${asset.name} · 已保存在本地素材库。`:'上传后先预览，点击 Apply & save 才会改变直播。';
        }
        this.updatePlayer();
        if([scene.background_asset,scene.music_asset].some(id=>id&&!this.assets.some(asset=>asset.id===id))) {
            this.load().catch(()=>this.studio.status('素材库读取失败，请稍后重新打开设置。',true));
        }
    }
    updatePlayer() {
        const id=this.$('scene-music-track').value;
        const url=/^[a-f0-9]{32}$/.test(id)?`/media/${id}.mp3`:'/static/music/conversation-lounge.mp3';
        const player=this.$('scene-music-preview');
        if(player.getAttribute('src')!==url){player.pause();player.src=url;}
    }
    async upload(kind,file) {
        if(this.busy)return;
        const status=this.$(`media-${kind}-status`),limit=(kind==='background'?20:50)*1024*1024;
        if(!file.name.toLowerCase().endsWith(kind==='background'?'.png':'.mp3')||!file.size||file.size>limit) {
            status.textContent=`请选择有效的 ${kind==='background'?'PNG（20 MB 内）':'MP3（50 MB 内）'} 文件。`;return;
        }
        this.busy=true;
        const revision=this.studio.profileContext?.revision;
        for(const id of ['media-background-upload','media-music-upload','direction-save','direction-discard'])this.$(id).disabled=true;
        status.textContent='Uploading · 正在保存到本地素材库…';
        try {
            const response=await fetch(`/api/media/${kind}`,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file,signal:AbortSignal.timeout(120000)});
            const data=await response.json();
            if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'上传失败，请重试。');
            this.assets.push(data.asset);this.syncOptions();
            if(this.studio.dialog.open&&this.studio.tab==='scene'&&this.studio.profileContext?.revision===revision) {
                if(kind==='background') {
                    this.$('scene-background-select').value='media:'+data.asset.id;
                    this.$('scene-effect').value='none';
                } else {
                    this.$('scene-music-track').value=data.asset.id;
                    this.$('scene-music-enabled').checked=true;this.updatePlayer();
                }
                this.studio.dirty=true;this.studio.outputs();this.studio.previewScene();
                this.studio.status('Upload ready · 点击 Apply & save 用于直播');
                status.textContent=`${file.name} · 已选入草稿，保存后生效。`;
            } else status.textContent=`${file.name} · 已存入素材库，可在列表中选择。`;
        } catch(error) {status.textContent=error.message||'上传失败，请重试。';}
        finally {
            this.busy=false;
            for(const id of ['media-background-upload','media-music-upload','direction-save','direction-discard'])this.$(id).disabled=false;
        }
    }
}
window.SharedMediaControls=SharedMediaControls;
