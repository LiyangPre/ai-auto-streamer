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
        this.$('api-tts-provider').addEventListener('change',()=>{this.$('api-tts-key').value='';this.$('api-tts-result').textContent='';this.update();});
        this.$('api-text-provider').addEventListener('change',()=>{
            this.$('api-text-key').value='';this.$('api-text-result').textContent='';
            const provider=this.$('api-text-provider').value;
            if(provider===this.data?.text.provider){this.$('api-text-url').value=this.data.text.base_url;this.$('api-text-model').value=this.data.text.model;this.$('api-text-route').value=this.data.text.network_mode;}
            else if(provider==='glm'){this.$('api-text-url').value='https://open.bigmodel.cn/api/paas/v4';this.$('api-text-model').value='glm-4.7-flashx';this.$('api-text-route').value='direct';}
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
    summary(data){this.$('api-summary').textContent=`${data.text.model} · Thinking off · ${data.active_tts==='none'?'Voice off':data.active_tts}`;}
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
        if(['mimo-tts','glm-tts','minimax'].includes(data.active_tts))this.$('api-tts-provider').value=data.active_tts;
        for(const side of ['text','tts'])this.$(`api-${side}-result`).textContent='';
        this.update();
    }
    update() {
        const provider=this.$('api-tts-provider').value;
        this.$('api-mimo-fields').hidden=provider!=='mimo-tts';this.$('api-glm-key-hint').hidden=provider!=='glm-tts';
        this.$('api-tts-key-status').textContent=this.data?.configured[provider]?'Configured · 已配置':'Not set · 未配置';
        const textProvider=this.$('api-text-provider').value;
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
            network_mode:this.$('api-text-route').value,enable_thinking:false},
            mimo:{base_url:this.$('api-mimo-url').value.trim(),network_mode:this.$('api-mimo-route').value},keys};
    }
    setBusy(value){this.busy=value;this.$('api-form').querySelectorAll('input,select,button:not(#api-close)').forEach(el=>el.disabled=value);}
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
            result.textContent=(data.ok?'✓ ':'')+data.message+(data.elapsed_ms?` · ${(data.elapsed_ms/1000).toFixed(2)}s`:'')+(data.route?' · '+data.route:'');
        } catch(error){if(generation===this.generation){result.classList.add('error');result.textContent=error.message;}}
        finally{this.setBusy(false);}
    }
}
window.addEventListener('DOMContentLoaded',()=>{window.apiControls=new APIControls();});
