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
        this.setupSongRequests();
        this.setupLyricsControls();
        this.setupPlaybackRange();
    }
    setupPlaybackRange(){
        const anchor=this.$('sing-guide-status');if(!anchor||this.$('sing-range-panel'))return;
        const panel=document.createElement('section');panel.id='sing-range-panel';panel.className='setting-note';
        const title=document.createElement('h4');title.textContent='Duration · 演唱时长';
        const label=document.createElement('label'),full=document.createElement('input');
        full.id='sing-range-full';full.type='checkbox';full.checked=true;label.append(full,document.createTextNode(' 完整唱完（默认）'));
        const hint=document.createElement('p');hint.className='api-hint';hint.textContent='取消勾选可指定原曲区间，支持 1:30.5 或 90.5 秒。每首歌独立保存，手动播放和点歌共用；当前演唱不受修改影响。';
        const fields=document.createElement('div');fields.className='settings-grid';
        for(const [name,text] of [['start','开始时间'],['end','结束时间（留空唱到结尾）']]){
            const field=document.createElement('label');field.textContent=text;
            const input=document.createElement('input');input.id='sing-range-'+name;input.type='text';input.inputMode='decimal';input.placeholder=name==='start'?'0:00':'唱到结尾';
            field.append(input);fields.append(field);
            input.addEventListener('input',()=>{this.rangeDirty=true;this.rangeLastError=null;this.showPlaybackRange(this.lyricSong());});
        }
        const actions=document.createElement('div');actions.className='module-actions';
        for(const [id,text,callback] of [
            ['mark-start','用试听位置设起点',()=>this.markRange('start')],
            ['mark-end','用试听位置设终点',()=>this.markRange('end')],
            ['preview','试听区间',()=>this.previewRange()],
            ['save','保存演唱区间',()=>this.savePlaybackRange()]]){
            const button=document.createElement('button');button.id='sing-range-'+id;button.type='button';button.className='text-button';button.textContent=text;button.onclick=callback;actions.append(button);
        }
        const status=document.createElement('p');status.id='sing-range-status';status.className='api-hint';status.setAttribute('role','status');
        panel.append(title,label,hint,fields,actions,status);anchor.after(panel);
        full.onchange=()=>{this.rangeDirty=true;this.rangeLastError=null;this.showPlaybackRange(this.lyricSong());};
        this.$('sing-audition').addEventListener('timeupdate',()=>{
            const audio=this.$('sing-audition');if(this.previewRangeEnd!=null&&audio.currentTime>=this.previewRangeEnd){audio.pause();this.previewRangeEnd=null;}
        });
    }
    timeLabel(seconds){const m=Math.floor(seconds/60),s=(seconds-m*60).toFixed(2).padStart(5,'0');return m+':'+s;}
    parseRangeTime(text){
        text=String(text).trim();
        if(!/^(?:\d+:)?\d+(?:\.\d{1,3})?$/.test(text))throw Error('时间请输入秒数或分:秒，例如 90 或 1:30。');
        const parts=text.split(':').map(Number);
        if(parts.length===2&&parts[1]>=60)throw Error('分:秒格式中的秒须小于 60。');
        const value=parts.length===2?parts[0]*60+parts[1]:parts[0];
        if(!Number.isFinite(value))throw Error('时间格式无效。');return value;
    }
    rangeDraft(song){
        if(!song)throw Error('请先选择歌曲。');
        const full=this.$('sing-range-full').checked;
        const start=full?0:this.parseRangeTime(this.$('sing-range-start').value);
        const end=full||!this.$('sing-range-end').value.trim()?null:this.parseRangeTime(this.$('sing-range-end').value);
        if(start>=song.duration||(end??song.duration)>song.duration||(end??song.duration)-start<.25)throw Error('区间须在原曲时长内，结束时间晚于开始时间至少 0.25 秒。');
        return {start_seconds:start,end_seconds:end};
    }
    showPlaybackRange(song){
        if(!this.$('sing-range-panel'))return;
        const key=this.lyricKey(song),saved=song?.playback_range||{start_seconds:0,end_seconds:null};
        if(key!==this.rangeSongKey){this.rangeSongKey=key;this.rangeDirty=false;this.rangeLastError=null;this.previewRangeEnd=null;}
        if(!this.rangeDirty){this.$('sing-range-full').checked=!saved.start_seconds&&saved.end_seconds==null;this.$('sing-range-start').value=this.timeLabel(saved.start_seconds||0);this.$('sing-range-end').value=saved.end_seconds==null?'':this.timeLabel(saved.end_seconds);}
        const busy=!!this.rangeBusy,full=this.$('sing-range-full').checked;
        this.$('sing-range-full').disabled=!song||busy;
        for(const name of ['start','end'])this.$('sing-range-'+name).disabled=!song||busy||full;
        for(const name of ['mark-start','mark-end','preview'])this.$('sing-range-'+name).disabled=!song||busy;
        this.$('sing-range-save').disabled=!song||busy||!this.rangeDirty;
        const status=this.$('sing-range-status');let invalid=false;
        try{const range=this.rangeDraft(song),end=range.end_seconds??song.duration;
            status.textContent=`原曲 ${this.timeLabel(song.duration)} · 演唱 ${this.timeLabel(end-range.start_seconds)} · ${this.timeLabel(range.start_seconds)}–${this.timeLabel(end)}${this.rangeDirty?' · 未保存':''}`;
        }catch(error){status.textContent=error.message;invalid=!!song;}
        if(this.rangeLastError){status.textContent=this.rangeLastError;invalid=true;}
        if(busy)status.textContent='正在保存演唱区间…';status.classList.toggle('error',invalid);
    }
    markRange(name){const song=this.lyricSong();if(!song)return;
        this.$('sing-range-full').checked=false;this.$('sing-range-'+name).value=this.timeLabel(this.$('sing-audition').currentTime||0);this.rangeDirty=true;this.rangeLastError=null;this.showPlaybackRange(song);}
    async previewRange(){try{const song=this.lyricSong(),range=this.rangeDraft(song),audio=this.$('sing-audition');
        this.previewRangeEnd=range.end_seconds??song.duration;audio.currentTime=range.start_seconds;await audio.play();
        }catch(error){this.rangeLastError=error.message;this.showPlaybackRange(this.lyricSong());}}
    async savePlaybackRange(){const song=this.lyricSong(),key=this.lyricKey(song);
        try{const playback_range=this.rangeDraft(song);this.rangeBusy=true;this.rangeLastError=null;this.showPlaybackRange(song);
            await this.request('/range',{song_id:song.id,playback_range});if(this.rangeSongKey===key)this.rangeDirty=false;await this.refresh();
        }catch(error){this.rangeLastError=error.message;}finally{this.rangeBusy=false;this.showPlaybackRange(this.lyricSong());}}
    setupLyricsControls(){
        const anchor=this.$('sing-guide-status');if(!anchor||this.$('sing-lrc-panel'))return;
        const panel=document.createElement('section');panel.id='sing-lrc-panel';panel.className='setting-note';
        const title=document.createElement('h4');title.textContent='Lyrics · 同步歌词';
        const hint=document.createElement('p');hint.className='api-hint';
        hint.textContent='上传与本录音版本一致的 .lrc，歌词按歌曲音频时间显示。支持 UTF-8、带 BOM 的 UTF-16 和常见中文编码；不会自动查找歌词或调用生成 API。';
        const file=document.createElement('input');file.id='sing-lrc-file';file.type='file';file.accept='.lrc,text/plain';file.hidden=true;
        const actions=document.createElement('div');actions.className='module-actions';
        const upload=document.createElement('button');upload.id='sing-lrc-upload';upload.type='button';upload.className='text-button';upload.textContent='Upload LRC · 上传歌词';
        const remove=document.createElement('button');remove.id='sing-lrc-remove';remove.type='button';remove.className='text-button';remove.textContent='Remove lyrics · 移除歌词';
        actions.append(upload,remove);
        const label=document.createElement('label');label.htmlFor='sing-lrc-offset';label.textContent='Timing offset · 歌词时间调整（秒）';
        const offset=document.createElement('input');offset.id='sing-lrc-offset';offset.type='number';offset.min='-30';offset.max='30';offset.step='0.1';offset.value='0';
        const timingHint=document.createElement('small');timingHint.className='api-hint';timingHint.textContent='正数延后、负数提前。例如 +0.5 表示歌词晚半秒显示；保存后立即生效，不重播歌曲。';
        const save=document.createElement('button');save.id='sing-lrc-save';save.type='button';save.className='soft-button';save.textContent='Apply timing · 应用时间';
        const status=document.createElement('p');status.id='sing-lrc-status';status.className='api-hint';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
        panel.append(title,hint,file,actions,label,offset,timingHint,save,status);anchor.after(panel);
        upload.onclick=()=>file.click();file.onchange=()=>this.uploadLyrics();
        remove.onclick=()=>this.removeLyrics();save.onclick=()=>this.saveLyricsOffset();
        offset.addEventListener('input',()=>{this.lyricsOffsetDirty=true;this.lyricsLastError=null;this.showLyrics(this.lyricSong());});
        this.showLyrics();
    }
    lyricSong(){return this.data?.songs?.find(song=>song.id===this.$('sing-songs').value);}
    lyricKey(song){return song?(this.data?.profile_id||'')+'|'+song.id:null;}
    showLyrics(song){
        if(!this.$('sing-lrc-panel'))return;
        const key=this.lyricKey(song),cues=Array.isArray(song?.lyrics_cues)?song.lyrics_cues:[],saved=Number.isFinite(song?.lyrics_offset)?song.lyrics_offset:0;
        if(key!==this.lyricsSongKey){this.lyricsSongKey=key;this.lyricsOffsetDirty=false;this.lyricsLastError=null;this.$('sing-lrc-offset').value=saved;}
        else if(!this.lyricsOffsetDirty)this.$('sing-lrc-offset').value=saved;
        const busy=!!this.lyricsBusy;
        this.$('sing-lrc-upload').disabled=!song||busy;
        this.$('sing-lrc-remove').disabled=!song||!cues.length||busy;
        this.$('sing-lrc-offset').disabled=!song||!cues.length||busy;
        this.$('sing-lrc-save').disabled=!song||!cues.length||!this.lyricsOffsetDirty||busy;
        this.$('sing-lrc-status').textContent=busy?'Saving lyrics · 正在保存歌词…':!song?'先选择当前角色的歌曲。':this.lyricsOffsetDirty?'Unsaved timing · 时间草稿已保留，应用后生效。':cues.length?`${cues.length} timed lines · 已连接同步歌词 · ${saved>=0?'+':''}${saved.toFixed(1)}s`:'还没有同步歌词。上传同版本录音的 LRC 后即可显示。';
        this.$('sing-lrc-status').classList.toggle('error',false);
        if(this.lyricsLastError&&!busy){this.$('sing-lrc-status').textContent=this.lyricsLastError;this.$('sing-lrc-status').classList.toggle('error',true);}
    }
    async readLrcFile(file){
        if(!file||!file.name.toLowerCase().endsWith('.lrc')||file.size>65536)throw Error('请选择 64 KB 以内的 .lrc 歌词文件。');
        const bytes=new Uint8Array(await file.arrayBuffer());let text;
        try{
            if(bytes[0]===0xff&&bytes[1]===0xfe)text=new TextDecoder('utf-16le',{fatal:true}).decode(bytes);
            else if(bytes[0]===0xfe&&bytes[1]===0xff)text=new TextDecoder('utf-16be',{fatal:true}).decode(bytes);
            else{try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{text=new TextDecoder('gb18030',{fatal:true}).decode(bytes);}}
        }catch{throw Error('歌词编码无法读取，请另存为 UTF-8 后上传。');}
        if(text.includes('\ufffd')||text.includes('\0'))throw Error('歌词包含无法识别的字符，请另存为 UTF-8 后上传。');
        if(text.length>30000||new TextEncoder().encode(text).byteLength>65536)throw Error('解码后的歌词应不超过 64 KB / 30000 字。');
        return text;
    }
    lyricsError(error){this.lyricsLastError=error.message;this.$('sing-lrc-status').textContent=error.message;this.$('sing-lrc-status').classList.toggle('error',true);}
    async uploadLyrics(){
        const song=this.lyricSong(),input=this.$('sing-lrc-file'),file=input.files?.[0];if(!song||!file)return;
        const key=this.lyricKey(song);this.lyricsLastError=null;this.lyricsBusy=true;this.showLyrics(song);
        try{
            const text=await this.readLrcFile(file);await this.request('/lyrics',{song_id:song.id,lrc_text:text});
            if(this.lyricsSongKey===key){this.lyricsSongKey=null;this.lyricsOffsetDirty=false;}
            this.lyricsBusy=false;await this.refresh();this.showLyrics(this.lyricSong());
        }catch(error){this.lyricsBusy=false;this.showLyrics(this.lyricSong());this.lyricsError(error);}
        finally{input.value='';}
    }
    async saveLyricsOffset(){
        const song=this.lyricSong();if(!song)return;const key=this.lyricKey(song),raw=String(this.$('sing-lrc-offset').value).trim(),seconds=Number(raw);
        if(!raw||!Number.isFinite(seconds)||seconds< -30||seconds>30){this.lyricsError(Error('歌词时间调整应为 -30 至 +30 秒。'));return;}
        this.lyricsLastError=null;this.lyricsBusy=true;this.showLyrics(song);
        try{
            await this.request('/lyrics/settings',{song_id:song.id,offset_seconds:seconds});
            if(this.lyricsSongKey===key){this.lyricsSongKey=null;this.lyricsOffsetDirty=false;}
            this.lyricsBusy=false;await this.refresh();this.showLyrics(this.lyricSong());
        }catch(error){this.lyricsBusy=false;this.showLyrics(this.lyricSong());this.lyricsError(error);}
    }
    async removeLyrics(){
        const song=this.lyricSong();if(!song)return;const key=this.lyricKey(song);this.lyricsLastError=null;this.lyricsBusy=true;this.showLyrics(song);
        try{
            await this.request('/lyrics/remove',{song_id:song.id});
            if(this.lyricsSongKey===key){this.lyricsSongKey=null;this.lyricsOffsetDirty=false;}
            this.lyricsBusy=false;await this.refresh();this.showLyrics(this.lyricSong());
        }catch(error){this.lyricsBusy=false;this.showLyrics(this.lyricSong());this.lyricsError(error);}
    }
    setupSongRequests(){
        if(!this.$('sing-request-card'))return;
        if(!this.$('sing-blocked-titles')){
            const label=document.createElement('label'),field=document.createElement('textarea'),hint=document.createElement('p');
            label.htmlFor='sing-blocked-titles';label.textContent='Blocked songs · 暂禁曲目（每行一首）';
            field.id='sing-blocked-titles';field.rows=3;field.maxLength=2600;field.placeholder='告白气球\n海屿你';
            hint.className='api-hint';hint.textContent='暂禁曲目不能点歌或在舞台播放，录音保留。删除对应行并保存即可解除。';
            this.$('sing-request-save').before(label,field,hint);
        }
        this.$('sing-request-add').onclick=()=>this.queueSelected();
        this.$('sing-request-clear').onclick=()=>this.requestAction('/requests/clear',{});
        this.$('sing-request-save').onclick=()=>this.saveRequestPolicy();
        for(const id of ['sing-chat-requests','sing-request-autoplay','sing-request-intermission','sing-blocked-titles']){
            this.$(id).addEventListener('input',()=>{
                this.requestPolicyDirty=true;this.$('sing-request-save').disabled=false;
                this.$('sing-request-policy-status').textContent='Unsaved · 点歌设置尚未保存';
            });
        }
    }
    showRequests(data){
        const panel=this.$('sing-request-card');if(!panel)return;
        const requests=data.requests;panel.hidden=!requests;if(!requests)return;
        const profileChanged=this.requestPolicyProfile!==data.profile_id;
        if(profileChanged||(!this.requestPolicyDirty&&this.requestPolicyRevision!==data.revision)){
            this.requestPolicy={...requests.policy,blocked_titles:[...(requests.policy.blocked_titles||[])]};this.requestPolicyProfile=data.profile_id;this.requestPolicyRevision=data.revision;
            this.$('sing-chat-requests').checked=!!requests.policy.chat_requests;
            this.$('sing-request-autoplay').checked=!!requests.policy.auto_play;
            this.$('sing-request-intermission').value=requests.policy.intermission_seconds;
            this.$('sing-blocked-titles').value=(requests.policy.blocked_titles||[]).join('\n');
            this.requestPolicyDirty=false;this.$('sing-request-save').disabled=true;
            this.$('sing-request-policy-status').textContent='Settings belong to this character · 当前角色独立保存';
        }else if(this.requestPolicyDirty&&this.requestPolicyRevision!==data.revision){
            this.$('sing-request-policy-status').textContent='其他设置已更新；你的草稿已保留。保存若提示版本冲突，请重新打开歌曲模块后再修改。';
        }
        const queue=Array.isArray(requests.queue)?requests.queue:[],list=this.$('sing-request-list');
        this.$('sing-request-count').textContent=`${queue.length} / ${requests.policy.max_queue}`;
        this.$('sing-request-clear').disabled=!queue.length;list.replaceChildren();
        if(!queue.length){const empty=document.createElement('li');empty.className='sing-request-empty';empty.textContent='还没有点歌。让观众发送“点歌 歌名”，或将所选曲目加入队列。';list.appendChild(empty);}
        for(const [index,item] of queue.entries()){
            const row=document.createElement('li'),copy=document.createElement('div'),title=document.createElement('strong'),requester=document.createElement('small'),remove=document.createElement('button');
            copy.className='sing-request-copy';title.textContent=`${index+1}. ${item.title}`;requester.textContent='Requested by · '+(item.requester||'Console');
            remove.type='button';remove.className='text-button';remove.textContent='Remove · 移除';remove.setAttribute('aria-label','移除点歌：'+item.title);
            remove.onclick=()=>this.requestAction('/requests/remove',{request_id:item.id});copy.append(title,requester);row.append(copy,remove);list.appendChild(row);
        }
        const result=requests.last_result;
        this.$('sing-request-result').textContent=typeof result?.message==='string'?result.message:'';
        this.$('sing-request-result').classList.toggle('error',result?.accepted===false);
    }
    async requestAction(path,body){
        try{const result=await this.request(path,body);await this.refresh();
            const message=result.message||result.result?.message;if(typeof message==='string')this.$('sing-request-result').textContent=message;
        }catch(error){this.$('sing-request-result').textContent=error.message;this.$('sing-request-result').classList.toggle('error',true);}
    }
    async queueSelected(){
        if(this.rangeDirty){this.$('sing-request-result').textContent='请先保存演唱区间。';return;}
        const songId=this.$('sing-songs').value;if(!songId)return;
        if(this.data?.songs?.find(song=>song.id===songId)?.blocked){
            this.$('sing-request-result').textContent='这首歌已暂禁，不能加入点歌队列。';return;
        }
        this.$('sing-request-add').disabled=true;await this.requestAction('/requests',{song_id:songId});this.select();
    }
    async saveRequestPolicy(){
        const status=this.$('sing-request-policy-status'),button=this.$('sing-request-save');
        try{
            if(!this.requestPolicy||this.requestPolicyProfile!==this.data?.profile_id)throw Error('请重新打开歌曲模块，加载当前角色的点歌设置。');
            const seconds=Number(this.$('sing-request-intermission').value);
            if(!Number.isFinite(seconds)||seconds<5||seconds>300)throw Error('歌曲间聊天时间应为 5–300 秒。');
            const blocked_titles=[...new Set(this.$('sing-blocked-titles').value.split(/\r?\n/).map(title=>title.trim()).filter(Boolean))];
            if(blocked_titles.length>32||blocked_titles.some(title=>title.length>80))throw Error('暂禁列表最多 32 首，每个歌名不超过 80 字。');
            const policy={...this.requestPolicy,chat_requests:this.$('sing-chat-requests').checked,auto_play:this.$('sing-request-autoplay').checked,intermission_seconds:seconds,blocked_titles};
            button.disabled=true;
            await this.request('/request-policy',{profile_id:this.requestPolicyProfile,revision:this.requestPolicyRevision,policy});
            this.requestPolicyDirty=false;this.requestPolicyProfile=null;await this.refresh();status.textContent='Saved · 当前角色点歌设置已保存';
        }catch(error){status.textContent=error.message;button.disabled=false;}
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
    async open(){this.dialog.showModal();this.requestPolicyProfile=null;this.requestPolicyDirty=false;await this.refresh();await this.media();}
    async media(){try{const response=await fetch('/api/media', {headers:{'X-Studio-API':'1'}});const data=await response.json(),select=this.$('sing-backing'),old=select.value;
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
            this.showRequests(data);
            const select=this.$('sing-songs'),old=select.value;select.replaceChildren(new Option('选择已生成的演唱',''));
            const visible=data.songs.filter(song=>song.source==='upload'||this.$('sing-show-generated').checked);
            for(const song of visible)select.add(new Option(`${song.blocked?'⛔ 已暂禁 · ':''}${song.title} · ${song.duration.toFixed(1)}s · ${song.source==='upload'?'Uploaded':song.voice}`,song.id));
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
    select(){const song=this.data?.songs.find(s=>s.id===this.$('sing-songs').value);this.$('sing-play').disabled=!song||!!song.blocked;
        if(this.$('sing-request-add'))this.$('sing-request-add').disabled=!song||!!song.blocked||!this.data?.requests;
        this.$('sing-guide-upload').disabled=!song;
        this.$('sing-guide-status').textContent=song?.guide_url?'已连接静音人声口型轨，整曲正常播放。':'未连接人声分轨：按整曲音量近似口型，伴奏也可能使嘴巴张开。';
        if(!song&&this.selected){this.$('sing-audition').pause();this.$('sing-audition').removeAttribute('src');this.selected=null;}
        if(song&&this.selected!==song.id){this.$('sing-audition').src=song.url;this.selected=song.id;}
        this.$('sing-song-info').textContent=song?`${song.blocked?'已暂禁舞台播放 · ':''}${song.duration.toFixed(1)}s · ${(song.bytes/1024).toFixed(0)} KB · ${song.source==='upload'?'Local recording · 本地录音':'生成耗时 '+(song.generation_ms/1000).toFixed(1)+'s'}`:'';
        this.showLyrics(song);this.showPlaybackRange(song);}
    async generate(){this.$('sing-generate').disabled=true;
        try{await this.request('/prepare',{title:this.$('sing-title').value.trim(),lyrics:this.$('sing-lyrics').value.trim(),style:this.$('sing-style').value.trim()});await this.refresh();}
        catch(error){this.status(error.message,true);this.$('sing-generate').disabled=false;}}
    async importTrack(guide){const input=this.$(guide?'sing-guide-file':'sing-track-file'),file=input.files[0];if(!file)return;
        const button=this.$(guide?'sing-guide-upload':'sing-track-upload');button.disabled=true;
        try{if(!file.name.toLowerCase().endsWith('.mp3')||file.size>20*1024*1024)throw Error('请选择 20 MB 以内的 MP3。');
            const response=await fetch('/api/media/music',{method:'POST',headers:{'X-Studio-API':'1','X-Filename':encodeURIComponent(file.name),'Content-Type':'audio/mpeg'},body:file});
            const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'上传失败');
            const imported=await this.request('/import',{asset_id:result.asset.id,song_id:guide?this.$('sing-songs').value:null});
            await this.refresh();this.$('sing-songs').value=imported.id;this.select();this.status(guide?'已连接人声口型轨；人声轨只分析口型，不另行播放。':'整曲已保存到当前角色歌库，播放不会调用 TTS。');
        }catch(error){this.status(error.message,true);}finally{button.disabled=false;input.value='';}}
    async action(path){try{await this.request(path,{});await this.refresh();}catch(error){this.status(error.message,true);}}
    async play(){this.$('sing-audition').pause();
        if(this.rangeDirty){this.status('请先保存演唱区间。',true);return;}
        if(this.data?.songs?.find(song=>song.id===this.$('sing-songs').value)?.blocked){this.status('这首歌已暂禁，不能在舞台播放。',true);return;}
        try{await this.request('/play',{song_id:this.$('sing-songs').value,music:this.$('sing-music').value,
            backing_id:this.$('sing-backing').value||null,backing_volume:Number(this.$('sing-back-volume').value),
            vocal_volume:Number(this.$('sing-vocal-volume').value),vocal_delay:Number(this.$('sing-delay').value),bpm:Number(this.$('sing-bpm').value)});
            this.status('已发送到输出页。若没有声音，点击输出画面一次。');await this.refresh();}
        catch(error){this.status(error.message,true);}}
    async upload(){const file=this.$('sing-file').files[0];if(!file)return;
        this.$('sing-upload').disabled=true;
        try{if(!file.name.toLowerCase().endsWith('.mp3')||file.size>20*1024*1024)throw Error('请选择不超过 20 MB 的 MP3。');
            const response=await fetch('/api/media/music',{method:'POST',headers:{'X-Studio-API':'1','X-Filename':encodeURIComponent(file.name),'Content-Type':'audio/mpeg'},body:file});
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
