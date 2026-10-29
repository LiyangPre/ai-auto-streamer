// Character-owned interaction policy; live counts never overwrite an edited form.
class InteractionControls {
    constructor() {
        this.$=id=>document.getElementById(id);this.form=this.$('interaction-form');
        this.data=null;this.editing=null;this.dirty=false;this.busy=false;this.loading=false;this.generation=0;
        this.form.addEventListener('submit',event=>{event.preventDefault();this.save();});
        this.form.addEventListener('input',()=>{this.dirty=true;this.summary();this.status('尚未保存 · 当前角色的互动设置');});
        this.$('interaction-reload').addEventListener('click',()=>this.load(true));
        this.refresh=()=>{if(!document.hidden)this.load();};
        this.timer=setInterval(this.refresh,5000);
        document.addEventListener('visibilitychange',this.refresh);
        window.addEventListener('studio-modules-changed',this.refresh);
        window.addEventListener('pagehide',()=>this.dispose());
        window.addEventListener('pageshow',event=>{if(event.persisted){this.closed=false;this.timer=setInterval(this.refresh,5000);this.load(true);}});
        this.load(true);
    }
    async request(body) {
        const response=await fetch('/api/interactions',{method:body?'POST':'GET',cache:'no-store',
            headers:{'X-Studio-API':'1','Content-Type':'application/json'},
            body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(10000)});
        const data=await response.json();
        if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'互动设置暂时不可用，请重试。');
        return data;
    }
    status(text,error=false) {
        this.$('interaction-status').textContent=text;
        this.$('interaction-status').classList.toggle('error',error);
    }
    fill(data) {
        const policy=data.policy;
        this.editing={profile_id:data.profile_id,revision:data.revision,policy:{...policy}};
        for(const name of ['enabled','narrate_scene_changes'])this.$('interaction-'+name).checked=!!policy[name];
        for(const name of ['threshold','window_seconds','trigger_cooldown','viewer_cooldown','selection_seconds'])
            this.$('interaction-'+name).value=policy[name];
        this.$('interaction-fields').disabled=false;this.dirty=false;this.summary();
    }
    summary() {
        const threshold=Number(this.$('interaction-threshold').value),windowSeconds=Number(this.$('interaction-window_seconds').value);
        this.$('interaction-summary').textContent=`${windowSeconds} 秒内累计 ${threshold} 次可信请求后准备节目；随口说“想听你唱歌”也算，不必使用命令。`;
    }
    renderState(data) {
        const state=data.state||{},counts=state.counts||{};
        this.$('interaction-profile').textContent=data.profile_id||'当前角色';
        this.$('interaction-singing-count').textContent=String(counts.singing||0);
        this.$('interaction-story-count').textContent=String(counts.story||0);
        const stateText=state.awaiting_song?`正在问大家想听什么歌 · 剩余 ${Math.ceil(state.selection_seconds_remaining||0)} 秒`
            :state.pending?`${state.pending.intent==='story'?'故事':'唱歌'}意愿已达到门槛 · 等待舞台空闲`
            :state.cooldown_remaining>0?`节目触发冷却 · ${Math.ceil(state.cooldown_remaining)} 秒`
            :data.policy?.enabled?'正在听大家想聊什么、听什么':'隐式节目意愿收集已关闭';
        if(this.$('interaction-live-state').textContent!==stateText)this.$('interaction-live-state').textContent=stateText;
    }
    async load(force=false) {
        if(this.closed||this.busy||this.loading)return;
        this.loading=true;const generation=++this.generation;
        try {
            const data=await this.request();if(this.closed||generation!==this.generation)return;
            const switched=this.editing&&this.editing.profile_id!==data.profile_id;
            this.data=data;this.renderState(data);
            if(force||!this.editing||switched||!this.dirty){
                this.fill(data);
                if(switched)this.status('角色已切换，已载入该角色的互动设置。');
                else if(force)this.status('设置保存在当前角色 · 显式点歌、故事命令仍单独处理');
            }
        } catch(error){if(!this.closed&&generation===this.generation)this.status(error.message,true);}
        finally{this.loading=false;}
    }
    collect() {
        const policy={...this.editing.policy};
        for(const name of ['enabled','narrate_scene_changes'])policy[name]=this.$('interaction-'+name).checked;
        for(const name of ['threshold','window_seconds','trigger_cooldown','viewer_cooldown','selection_seconds'])
            policy[name]=Number(this.$('interaction-'+name).value);
        return {profile_id:this.editing.profile_id,revision:this.editing.revision,policy};
    }
    async save() {
        if(this.closed||this.busy||!this.editing||!this.form.reportValidity())return;
        const body=this.collect(),generation=++this.generation;
        this.busy=true;this.$('interaction-fields').disabled=true;this.status('正在保存…');
        try {
            const data=await this.request(body);if(this.closed||generation!==this.generation)return;
            this.data=data;this.fill(data);this.renderState(data);this.status('已保存 · 新的互动规则立即生效');
            window.dispatchEvent(new Event('studio-modules-changed'));
        } catch(error){if(!this.closed&&generation===this.generation)this.status(error.message+' 如已在别处修改，请先点“重新载入”。',true);}
        finally{this.busy=false;if(!this.closed)this.$('interaction-fields').disabled=false;}
    }
    dispose(){this.closed=true;++this.generation;clearInterval(this.timer);}
}
if(typeof window!=='undefined')window.addEventListener('DOMContentLoaded',()=>{
    if(document.getElementById('interaction-form'))window.interactionControls=new InteractionControls();
});
if(typeof module!=='undefined'&&module.exports)module.exports={InteractionControls};
