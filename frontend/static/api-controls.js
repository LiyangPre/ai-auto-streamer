class APIControls {
    constructor() {
        this.$=id=>document.getElementById(id);this.dialog=this.$('api-dialog');this.generation=0;
        this.$('api-open').addEventListener('click',()=>this.open());
        this.$('api-close').addEventListener('click',()=>this.dialog.close());
        this.dialog.addEventListener('close',()=>{++this.generation;this.clearKeys();});
        this.$('api-reload').addEventListener('click',()=>this.load());
        this.$('api-form').addEventListener('submit',event=>{event.preventDefault();this.save();});
        this.$('api-test-text').addEventListener('click',()=>this.test('text'));
        this.$('api-test-tts').addEventListener('click',()=>this.test(this.$('api-tts-provider').value));
        this.$('api-fallback-preset').addEventListener('click',()=>{
            const order=this.data?.glm_default_order||['glm-4.7-flash','glm-4-flash-250414','glm-4-flashx-250414','glm-4-air-250414','glm-4.5-air','glm-4.7-flashx'];
            this.$('api-text-model').value=order[0];this.$('api-text-fallbacks').value=order.slice(1).join('\n');
            this.$('api-text-attempt-timeout').value=3;this.$('api-text-total-timeout').value=20;
        });
        this.$('api-tts-provider').addEventListener('change',()=>{this.$('api-tts-key').value='';this.$('api-tts-result').textContent='';this.update();});
        this.$('api-text-provider').addEventListener('change',()=>{
            this.$('api-text-key').value='';this.$('api-text-result').textContent='';
            const provider=this.$('api-text-provider').value;
            if(provider===this.data?.text.provider){this.$('api-text-url').value=this.data.text.base_url;this.$('api-text-model').value=this.data.text.model;this.$('api-text-route').value=this.data.text.network_mode;}
            else if(provider==='glm'){this.$('api-text-url').value='https://open.bigmodel.cn/api/paas/v4';this.$('api-text-model').value='glm-4.7-flash';this.$('api-text-route').value='direct';}
            else if(provider==='ollama'){this.$('api-text-url').value='http://127.0.0.1:11434/v1';this.$('api-text-model').value='qwen2.5:7b-instruct';}
            else if(provider==='mock'){this.$('api-text-url').value='http://127.0.0.1';this.$('api-text-model').value='mock';}
            else {this.$('api-text-url').value='';this.$('api-text-model').value='';}
            this.update();
        });
        this.refreshSummary();
    }
    clearKeys(){this.$('api-text-key').value='';this.$('api-tts-key').value='';}
    async request(path='',body) {
        const response=await fetch('/api/connections'+path,{method:body?'POST':'GET',cache:'no-store',
            headers:{'Content-Type':'application/json','X-Studio-API':'1'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(35000)});
        const value=await response.json();
        if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'API settings request failed.');
        return value;
    }
    summary(data){this.$('api-summary').textContent=`${data.text_runtime?.last_model||data.text.model}${data.text.provider==='glm'&&data.text.fallback_models?.length?' · Auto fallback':''} · Thinking off · ${data.active_tts==='none'?'Voice off':data.active_tts}`;}
    async refreshSummary(){try{this.summary(await this.request());}catch{this.$('api-summary').textContent='Text & TTS · 打开接口设置查看连接状态';}}
    async open(){this.dialog.showModal();await this.load();}
    async load() {
        this.clearKeys();this.setBusy(true);
        try{this.fill(await this.request());this.status('Loaded · 密钥留空即保留。保存后立即生效。');}
        catch(error){this.status(error.message,true);}
        finally{this.setBusy(false);}
    }
    fill(data) {
        this.data=data;this.summary(data);this.clearKeys();
        for(const [id,key] of [['provider','provider'],['url','base_url'],['model','model'],['route','network_mode']])this.$('api-text-'+id).value=data.text[key];
        this.$('api-mimo-url').value=data.mimo.base_url;this.$('api-mimo-route').value=data.mimo.network_mode;
        this.$('api-text-fallbacks').value=(data.text.fallback_models||[]).join('\n');
        this.$('api-text-attempt-timeout').value=data.text.fallback_attempt_timeout??3;
        this.$('api-text-total-timeout').value=data.text.fallback_total_timeout??20;
        this.$('api-delay-notice').checked=data.text.delay_notice_enabled!==false;
        const runtime=data.text_runtime;
        this.$('api-fallback-runtime').textContent=(runtime?.last_model?'最近成功：'+runtime.last_model+'。 ':'')+Object.entries(runtime?.cooldowns||{}).map(([model,seconds])=>`${model} 冷却 ${Math.ceil(seconds)}s`).join(' · ');
        this.renderCatalog(data.glm_catalog||[]);
        if(['mimo-tts','glm-tts','minimax'].includes(data.active_tts))this.$('api-tts-provider').value=data.active_tts;
        for(const side of ['text','tts'])this.$(`api-${side}-result`).textContent='';
        this.update();
    }
    update() {
        const provider=this.$('api-tts-provider').value;
        this.$('api-mimo-fields').hidden=provider!=='mimo-tts';this.$('api-glm-key-hint').hidden=provider!=='glm-tts';
        this.$('api-tts-key-status').textContent=this.data?.configured[provider]?'Configured · 已配置':'Not set · 未配置';
        const textProvider=this.$('api-text-provider').value;
        this.$('api-glm-fallback-fields').hidden=textProvider!=='glm';
        this.$('api-text-key-status').textContent=['mock','ollama'].includes(textProvider)?'Not required · 无需密钥':
            textProvider!==this.data?.text.provider?'Test to check · 请测试':this.data?.configured.text?'Configured · 已配置':'Not set · 未配置';
        this.$('api-route-summary').textContent='保存的路由：Text '+(this.data?.routes.text||'—')+' / MiMo '+(this.data?.routes.mimo||'—');
    }
    collect() {
        const keys={};
        const text=this.$('api-text-key').value.trim(),tts=this.$('api-tts-key').value.trim();
        if(text)keys.text=text;if(tts)keys[this.$('api-tts-provider').value]=tts;
        return {revision:this.data.revision,text:{provider:this.$('api-text-provider').value,
            base_url:this.$('api-text-url').value.trim(),model:this.$('api-text-model').value.trim(),
            network_mode:this.$('api-text-route').value,enable_thinking:false,
            fallback_models:this.$('api-text-provider').value==='glm'?this.$('api-text-fallbacks').value.split(/[\s,，]+/).filter(Boolean):[],
            fallback_attempt_timeout:Number(this.$('api-text-attempt-timeout').value),
            fallback_total_timeout:Number(this.$('api-text-total-timeout').value),
            fallback_cooldown:this.data.text.fallback_cooldown??90,
            delay_notice_enabled:this.$('api-delay-notice').checked,
            delay_notice_after:this.data.text.delay_notice_after??3,
            delay_notice_cooldown:this.data.text.delay_notice_cooldown??45},
            mimo:{base_url:this.$('api-mimo-url').value.trim(),network_mode:this.$('api-mimo-route').value},keys};
    }
    renderCatalog(models){
        const box=this.$('api-glm-catalog');box.replaceChildren();
        const table=document.createElement('table');table.className='api-model-table';
        for(const row of [['Model','输入 / 输出','总 / 激活参数'],...models.map(model=>[model.id,`${model.input_price} / ${model.output_price}`,model.total_b?`${model.total_b}B / ${model.active_b}B`:'未公开'])]){
            const tr=document.createElement('tr');for(const text of row){const td=document.createElement('td');td.textContent=text;tr.appendChild(td);}table.appendChild(tr);
        }
        box.appendChild(table);
    }
    setBusy(value){this.busy=value;this.$('api-form').querySelectorAll('input,select,textarea,button:not(#api-close)').forEach(el=>el.disabled=value);}
    status(message,error=false){this.$('api-status').textContent=message;this.$('api-status').classList.toggle('error',error);}
    async save() {
        if(this.busy||!this.data)return;
        const body=this.collect();this.setBusy(true);
        try {
            this.fill(await this.request('',body));this.status('Saved locally · 已立即生效，密钥不会回传到页面。');
            window.controlPanel?.loadConfig();
            if(window.studioControls&&!window.studioControls.dirty)await window.studioControls.load();
        } catch(error){this.status(error.message,true);}
        finally{this.setBusy(false);}
    }
    async test(target) {
        if(this.busy||!this.data)return;
        const result=this.$(target==='text'?'api-text-result':'api-tts-result'),generation=this.generation;
        const body={...this.collect(),target};this.setBusy(true);result.classList.remove('error');result.textContent='Testing · 正在检查当前草稿…';
        try {
            const data=await this.request('/test',body);
            if(generation!==this.generation)return;
            result.classList.toggle('error',!data.ok);
            const first=data.first_text_ms??data.first_audio_ms;
            const firstLabel=data.first_text_ms!==undefined?'首字':'首段语音';
            result.textContent=(data.ok?'✓ ':'')+data.message+(data.model?' · '+data.model:'')+(Number.isFinite(first)?` · ${firstLabel} ${(first/1000).toFixed(2)}s`:'')+(data.elapsed_ms?` · 总计 ${(data.elapsed_ms/1000).toFixed(2)}s`:'')+(data.route?' · '+data.route:'')+(data.attempts?.length?' · '+data.attempts.map(item=>item.model+': '+(item.ok?'✓':item.code)).join(' → '):'');
        } catch(error){if(generation===this.generation){result.classList.add('error');result.textContent=error.message;}}
        finally{this.setBusy(false);}
    }
}
window.addEventListener('DOMContentLoaded',()=>{window.apiControls=new APIControls();});
