class SingingControls {
    constructor(){
        this.$=id=>document.getElementById(id);this.dialog=this.$('sing-dialog');
        this.$('sing-close').onclick=()=>this.dialog.close();
        this.dialog.addEventListener('close',()=>{clearTimeout(this.poll);this.$('sing-audition').pause();});
        this.$('sing-form').onsubmit=event=>{event.preventDefault();this.generate();};
        this.$('sing-demo').onclick=()=>{this.$('sing-title').value='银龙晚风 · 原创测试';this.$('sing-lyrics').value=this.data?.demo_lyrics||'';};
        this.$('sing-cancel').onclick=()=>this.action('/cancel');
        this.$('sing-stop').onclick=()=>this.action('/stop');
        this.$('sing-play').onclick=()=>this.play();
        this.$('sing-songs').onchange=()=>this.select();
        this.$('sing-music').onchange=()=>this.$('sing-backing-fields').hidden=this.$('sing-music').value!=='accompaniment';
        this.$('sing-upload').onclick=()=>this.$('sing-file').click();
        this.$('sing-file').onchange=()=>this.upload();
        this.$('sing-backup').onclick=()=>this.backup();
        this.$('sing-track-upload').onclick=()=>this.$('sing-track-file').click();
        this.$('sing-guide-upload').onclick=()=>this.$('sing-guide-file').click();
        this.$('sing-track-file').onchange=()=>this.importTrack(false);
        this.$('sing-guide-file').onchange=()=>this.importTrack(true);
        this.$('sing-show-generated').onchange=()=>this.refresh();
        this.setupPreparationStatus();
    }
    setupPreparationStatus(){
        const owner=this.$('sing-library-owner');if(!owner)return;
        const panel=document.createElement('div');panel.className='setting-note';panel.setAttribute('role','status');panel.setAttribute('aria-live','polite');
        const heading=document.createElement('strong');heading.textContent='Song preparation · 歌曲准备';
        this.preparationText=document.createElement('p');
        this.preparationProgress=document.createElement('progress');this.preparationProgress.max=1;this.preparationProgress.value=0;this.preparationProgress.setAttribute('aria-label','离线歌曲准备进度');
        const hint=document.createElement('small');hint.textContent='后台准备完成后保存到 Astra 歌库，不会自动播放。';
        panel.append(heading,this.preparationText,this.preparationProgress,hint);owner.before(panel);this.preparationPanel=panel;
        this.showPreparation();
    }
    showPreparation(value={}){
        if(!this.preparationPanel)return;
        const total=Number.isFinite(value.total)&&value.total>0?Math.min(64,value.total):0;
        const ready=Number.isFinite(value.ready)?Math.min(total,Math.max(0,value.ready)):0;
        const labels={waiting:'等待准备',separating:'分离人声',converting:'转换音色',mixing:'混音',importing:'保存歌库',complete:'已完成',ready:'已完成',running:'准备中',error:'准备失败',failed:'准备失败',paused:'已暂停'};
        const stage=labels[value.stage]||labels[value.state]||'准备中';
        const parts=[`${ready} / ${total} · ${stage}`];
        if(typeof value.current_title==='string'&&value.current_title)parts.push(value.current_title);
        if(typeof value.message==='string'&&value.message)parts.push(value.message);
        if(Number.isFinite(value.estimated_seconds_remaining)&&value.estimated_seconds_remaining>0){const seconds=value.estimated_seconds_remaining;parts.push(seconds>=60?`预计剩余 ${Math.ceil(seconds/60)} 分钟`:`预计剩余 ${Math.ceil(seconds)} 秒`);}
        const text=parts.join(' · ');if(this.preparationText.textContent!==text)this.preparationText.textContent=text;
        this.preparationProgress.max=total||1;this.preparationProgress.value=ready;
        this.preparationPanel.classList.toggle('error',['error','failed'].includes(value.state));
    }
    status(text,error=false){this.$('sing-status').textContent=text;this.$('sing-status').classList.toggle('error',error);}
    async request(path='',body){const response=await fetch('/api/singing'+path,{method:body===undefined?'GET':'POST',
        headers:{'Content-Type':'application/json','X-Studio-API':'1'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(10000)});
        const value=await response.json();if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'请求失败，请检查输入。');return value;}
    async open(){this.dialog.showModal();await this.refresh();await this.media();}
    async media(){try{const response=await fetch('/api/media');const data=await response.json(),select=this.$('sing-backing'),old=select.value;
        select.replaceChildren(new Option('选择已上传的 MP3 伴奏',''));
        for(const item of data.assets.filter(a=>a.kind==='music'))select.add(new Option(`${item.name} · ${Math.round(item.duration)}s`,item.id));
        if([...select.options].some(o=>o.value===old))select.value=old;
    }catch{this.status('素材库读取失败。',true);}}
    async refresh(){
        clearTimeout(this.poll);
        try{
            const data=await this.request(),changed=JSON.stringify(data.job)!==JSON.stringify(this.data?.job);
            if(this.data?.profile_id&&this.data.profile_id!==data.profile_id){this.$('sing-audition').pause();this.$('sing-audition').removeAttribute('src');this.selected=null;}
            this.data=data;
            this.showPreparation(data.preparation);
            const select=this.$('sing-songs'),old=select.value;select.replaceChildren(new Option('选择已生成的演唱',''));
            const visible=data.songs.filter(song=>song.source==='upload'||this.$('sing-show-generated').checked);
            for(const song of visible)select.add(new Option(`${song.title} · ${song.duration.toFixed(1)}s · ${song.source==='upload'?'Uploaded':song.voice}`,song.id));
            select.value=visible.some(s=>s.id===old)?old:(visible[0]?.id||'');this.select();
            const busy=data.job.state==='preparing';this.$('sing-generate').disabled=busy;this.$('sing-cancel').disabled=!busy;
            this.$('sing-stop').disabled=!data.playback;
            StudioModules.setStatus('singing',data.playback?'♫ 演唱中 · 聊天回复暂缓':busy?'生成中 · 不影响当前聊天':`${data.songs.length} saved takes · 已保存曲目`);
            this.$('sing-library-owner').textContent=(data.profile_name||'Character')+' · SONG LIBRARY';
            this.$('sing-backup').disabled=!data.songs.length;
            if(data.job.state==='error'&&changed)this.status(data.job.message||'生成失败，请重试。',true);
            else if(busy)this.status(`正在生成演唱音频… ${Math.max(0,Math.round(Date.now()/1000-data.job.started))} 秒（最多等待 4 分钟）`);
            else if(data.job.state==='ready'&&changed)this.status('人声已生成。先检查有没有漏唱，再选择伴奏并在舞台播放。');
            if(!this.$('sing-lyrics').value)this.$('sing-lyrics').value=data.demo_lyrics;
        }catch(error){this.status(error.message,true);}
        if(this.dialog.open)this.poll=setTimeout(()=>this.refresh(),2000);
    }
    select(){const song=this.data?.songs.find(s=>s.id===this.$('sing-songs').value);this.$('sing-play').disabled=!song;
        this.$('sing-guide-upload').disabled=!song;
        this.$('sing-guide-status').textContent=song?.guide_url?'已连接静音人声口型轨，整曲正常播放。':'未连接人声分轨：按整曲音量近似口型，伴奏也可能使嘴巴张开。';
        if(!song&&this.selected){this.$('sing-audition').pause();this.$('sing-audition').removeAttribute('src');this.selected=null;}
        if(song&&this.selected!==song.id){this.$('sing-audition').src=song.url;this.selected=song.id;}
        this.$('sing-song-info').textContent=song?`${song.duration.toFixed(1)}s · ${(song.bytes/1024).toFixed(0)} KB · ${song.source==='upload'?'Local recording · 本地录音':'生成耗时 '+(song.generation_ms/1000).toFixed(1)+'s'}`:'';}
    async generate(){this.$('sing-generate').disabled=true;
        try{await this.request('/prepare',{title:this.$('sing-title').value.trim(),lyrics:this.$('sing-lyrics').value.trim(),style:this.$('sing-style').value.trim()});await this.refresh();}
        catch(error){this.status(error.message,true);this.$('sing-generate').disabled=false;}}
    async importTrack(guide){const input=this.$(guide?'sing-guide-file':'sing-track-file'),file=input.files[0];if(!file)return;
        const button=this.$(guide?'sing-guide-upload':'sing-track-upload');button.disabled=true;
        try{if(!file.name.toLowerCase().endsWith('.mp3')||file.size>20*1024*1024)throw Error('请选择 20 MB 以内的 MP3。');
            const response=await fetch('/api/media/music',{method:'POST',headers:{'X-Filename':encodeURIComponent(file.name),'Content-Type':'audio/mpeg'},body:file});
            const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'上传失败');
            const imported=await this.request('/import',{asset_id:result.asset.id,song_id:guide?this.$('sing-songs').value:null});
            await this.refresh();this.$('sing-songs').value=imported.id;this.select();this.status(guide?'已连接人声口型轨；人声轨只分析口型，不另行播放。':'整曲已保存到当前角色歌库，播放不会调用 TTS。');
        }catch(error){this.status(error.message,true);}finally{button.disabled=false;input.value='';}}
    async action(path){try{await this.request(path,{});await this.refresh();}catch(error){this.status(error.message,true);}}
    async play(){this.$('sing-audition').pause();
        try{await this.request('/play',{song_id:this.$('sing-songs').value,music:this.$('sing-music').value,
            backing_id:this.$('sing-backing').value||null,backing_volume:Number(this.$('sing-back-volume').value),
            vocal_volume:Number(this.$('sing-vocal-volume').value),vocal_delay:Number(this.$('sing-delay').value),bpm:Number(this.$('sing-bpm').value)});
            this.status('已发送到输出页。若没有声音，点击输出画面一次。');await this.refresh();}
        catch(error){this.status(error.message,true);}}
    async upload(){const file=this.$('sing-file').files[0];if(!file)return;
        this.$('sing-upload').disabled=true;
        try{if(!file.name.toLowerCase().endsWith('.mp3')||file.size>20*1024*1024)throw Error('请选择不超过 20 MB 的 MP3。');
            const response=await fetch('/api/media/music',{method:'POST',headers:{'X-Filename':encodeURIComponent(file.name),'Content-Type':'audio/mpeg'},body:file});
            const result=await response.json();if(!response.ok)throw Error(result.detail||'上传失败');
            await this.media();this.$('sing-backing').value=result.asset.id;this.status('伴奏已上传，需先试听并调整入点；不会自动匹配旋律。');
        }catch(error){this.status(error.message,true);}finally{this.$('sing-upload').disabled=false;this.$('sing-file').value='';}}
    async backup(){this.$('sing-backup').disabled=true;
        try{const result=await this.request('/backup',{});
            const response=await fetch(result.url,{headers:{'X-Studio-API':'1'}});if(!response.ok)throw Error('备份已保存到本机，但下载失败。');
            const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');link.href=url;link.download=result.filename;
            document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
            this.status(`已备份 ${result.count} 首人声：${result.filename}。本机备份也已保存。`);
        }catch(error){this.status(error.message,true);}finally{this.$('sing-backup').disabled=false;}}
}
StudioModules.registerController('singing',()=>{window.singingControls=new SingingControls();return window.singingControls;});
