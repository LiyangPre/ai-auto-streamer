class StoryControls{
    constructor(){
        const dialog=document.createElement('dialog');dialog.id='story-dialog';dialog.setAttribute('aria-labelledby','story-heading');
        dialog.innerHTML=`<div class="story-shell"><header class="api-heading"><div><div class="eyebrow">STORY MODULE · 三人合租日记</div><h2 id="story-heading">Stories from our little home.</h2><p>只有 Astra、Claude 和 DeepSeek 的合租小日常：有点好笑，有点可爱，也有一起闯的小祸。</p></div><button type="button" id="story-close" class="text-button" aria-label="关闭故事模块">×</button></header>
        <div class="api-body"><div class="api-columns"><section class="api-card"><div class="eyebrow">01 / PREPARED STORIES</div><h3>Pick a little adventure.</h3><label for="story-select">Story · 故事</label><select id="story-select"></select><p id="story-ready" class="api-hint"></p><audio id="story-audition" controls preload="none" aria-label="试听故事"></audio><label for="story-voice">Voice · 当前角色已保存的声音</label><textarea id="story-voice" rows="3" readonly></textarea><label for="story-script">Story script · 送入 TTS 的剧本</label><textarea id="story-script" rows="8" readonly></textarea><div class="module-actions"><button type="button" id="story-random" class="soft-button">Random story · 随机讲一个</button><button type="button" id="story-play" class="soft-button">Tell selected · 讲这个</button><button type="button" id="story-stop" class="text-button">Stop · 回到聊天</button></div><div class="module-actions"><button type="button" id="story-prepare" class="text-button">Prepare ten · 准备当前声音的十个故事</button><button type="button" id="story-cancel" class="text-button">取消准备</button></div><p class="api-hint">Voice Design 先完整生成，再在本地重放。十个故事会轮换随机抽取，一轮内不重复；严格沿用 Voice 面板已保存的声音描述，不追加故事专用角色或音色；改声音后需要重新准备，旧声音不会混入。准备缺失音频时才调用 API。</p></section>
        <section class="api-card"><div class="eyebrow">02 / TRIGGERS & INTERRUPTIONS</div><h3>When should Astra tell a story?</h3><label class="story-check"><input type="checkbox" id="story-chat-requests">允许观众请求故事</label><p class="api-hint">观众可说“讲个故事”“换个故事”“停止故事”。正在讲时不重复启动；故事欢迎插播结束后自动接着讲。</p><label for="story-request-cooldown">Request cooldown · 请求间隔（秒）</label><input id="story-request-cooldown" type="number" min="5" max="600" value="45"><label class="story-check"><input type="checkbox" id="story-auto-idle">安静时自动随机讲故事（默认关闭）</label><label for="story-idle-seconds">Quiet time · 安静多久后开始（秒）</label><input id="story-idle-seconds" type="number" min="30" max="3600" value="120"><label for="story-auto-interval">Auto interval · 自动故事最短间隔（秒）</label><input id="story-auto-interval" type="number" min="120" max="7200" value="600"><button id="story-save" type="button" class="soft-button">Save triggers · 保存触发方式</button><p class="api-hint">观众进场时先准备欢迎语，再暂停故事、欢迎、接着原位置讲。5 秒收集进场，最多念 3 个名字，拥挤时合并欢迎；唱歌期间不插播。故事结束自动回到聊天。</p><p id="story-policy-status" role="status" class="api-hint"></p></section></div></div><footer class="api-footer"><span id="story-status" role="status">Loading stories…</span><a class="text-button" href="/?overlay=1&controls=0" target="_blank" rel="noopener">Open output ↗</a></footer></div>`;
        document.body.appendChild(dialog);this.dialog=dialog;this.$=id=>document.getElementById(id);
        this.$('story-close').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{clearTimeout(this.poll);this.$('story-audition').pause();});
        this.$('story-select').onchange=()=>this.select();this.$('story-random').onclick=()=>this.play(null);
        this.$('story-play').onclick=()=>this.play(this.$('story-select').value);this.$('story-stop').onclick=()=>this.action('/stop');
        this.$('story-prepare').onclick=()=>this.action('/prepare');this.$('story-cancel').onclick=()=>this.action('/cancel');
        this.$('story-save').onclick=()=>this.save();
    }
    async request(path='',body){const response=await fetch('/api/stories'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Studio-API':'1'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(10000)});
        const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'故事请求失败');return result;}
    async open(){this.dialog.showModal();this.filled=null;await this.refresh();}
    async refresh(){clearTimeout(this.poll);try{
        const data=await this.request();this.data=data;this.$('story-voice').value=data.voice?.description||'';const select=this.$('story-select'),old=select.value;
        select.replaceChildren(...data.stories.map(story=>new Option((story.ready?'✓ ':'○ ')+story.title,story.id)));if(data.stories.some(story=>story.id===old))select.value=old;this.select();
        const ready=data.stories.filter(story=>story.ready).length,busy=data.job.state==='preparing';
        this.$('story-ready').textContent=ready+' / 10 stories ready · 本角色的声音';
        this.$('story-random').disabled=!ready;this.$('story-stop').disabled=!data.playback;
        this.$('story-prepare').disabled=busy;this.$('story-cancel').disabled=!busy;
        if(this.filled!==data.profile_id){const policy=data.policy;this.$('story-chat-requests').checked=policy.chat_requests;this.$('story-auto-idle').checked=policy.auto_idle;
            for(const key of ['request_cooldown','idle_seconds','auto_interval'])this.$('story-'+key.replaceAll('_','-') ).value=policy[key];this.filled=data.profile_id;}
        this.$('story-status').textContent=busy?'正在准备 '+data.job.completed+'/10：'+data.job.title:data.playback?(data.playback.paused?'欢迎新观众 · 随后接着原故事':'正在讲：'+data.playback.song.title):ready+'/10 已准备 · 可随机播放';
        StudioModules.setStatus('story',data.playback?'☾ Telling a story':ready+'/10 ready · 故事待命');
    }catch(error){this.$('story-status').textContent=error.message;}if(this.dialog.open)this.poll=setTimeout(()=>this.refresh(),2000);}
    select(){const story=this.data?.stories.find(entry=>entry.id===this.$('story-select').value);if(!story)return;
        this.$('story-script').value=story.text;this.$('story-play').disabled=!story.ready;
        if(this.selected!==story.clip?.id){const audio=this.$('story-audition');audio.pause();if(story.ready)audio.src=story.clip.url;else audio.removeAttribute('src');this.selected=story.clip?.id;}}
    async play(id){this.$('story-audition').pause();try{await this.request('/play',{story_id:id});await this.refresh();}catch(error){this.$('story-status').textContent=error.message;}}
    async action(path){try{await this.request(path,{});await this.refresh();}catch(error){this.$('story-status').textContent=error.message;}}
    async save(){try{const policy={chat_requests:this.$('story-chat-requests').checked,auto_idle:this.$('story-auto-idle').checked};for(const key of ['request_cooldown','idle_seconds','auto_interval'])policy[key]=Number(this.$('story-'+key.replaceAll('_','-')).value);
        await this.request('/settings',{profile_id:this.data.profile_id,revision:this.data.revision,policy});this.$('story-policy-status').textContent='Saved · 当前角色触发方式已保存';await this.refresh();
    }catch(error){this.$('story-policy-status').textContent=error.message;}}
}
StudioModules.registerController('story',()=>new StoryControls());
