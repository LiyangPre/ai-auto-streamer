// Installed modules register their own controller and stage adapters.
const StudioModules = {
    controllers:new Map(), outputs:new Map(), scripts:new Map(), instances:new Map(),
    registerController(id,factory){if(this.controllers.has(id))throw Error('Duplicate controller module');this.controllers.set(id,factory);},
    registerOutput(id,factory){if(this.outputs.has(id))throw Error('Duplicate output module');this.outputs.set(id,factory);},
    script(path){
        if(!/^\/static\/[a-z0-9/_-]+\.js$/.test(path))return Promise.reject(Error('Invalid module script'));
        if(!this.scripts.has(path))this.scripts.set(path,new Promise((resolve,reject)=>{
            const element=document.createElement('script');element.src=path+'?v=singing-room-1';element.onload=resolve;
            element.onerror=()=>reject(Error('Module could not load'));document.head.appendChild(element);
        }));return this.scripts.get(path);
    },
    async manifest(){const response=await fetch('/api/modules',{headers:{'X-Studio-API':'1'},signal:AbortSignal.timeout(8000)});
        if(!response.ok)throw Error('Modules unavailable. Restart Studio after updating.');return response.json();},
    setStatus(id,text){const target=document.getElementById('module-status-'+id);if(target)target.textContent=text;}
};
window.StudioModules=StudioModules;

class StageModuleHost {
    constructor(client){this.client=client;this.instances=new Map();this.presentations={};}
    async init(){
        const manifest=await StudioModules.manifest();this.configure(manifest);
        for(const spec of manifest.modules){
            if(!spec.output)continue;
            await StudioModules.script(spec.output);
            const factory=StudioModules.outputs.get(spec.id);if(!factory)throw Error('Module did not register');
            this.instances.set(spec.id,await factory(this.client));
        }
    }
    configure(manifest){for(const spec of manifest?.modules||[])this.presentations[spec.id]=spec.presentation;}
    presentation(id){return this.presentations[id]||{};}
    handle(event){
        if(event.modules)this.configure(event.modules);
        let consumed=false;
        for(const instance of this.instances.values())consumed=instance.handle?.(event)||consumed;
        return consumed||!!this.active&&['idle_action','response','response_start','response_chunk','response_end','audio_segment','thinking_start','thinking_end'].includes(event.type);
    }
    get active(){return [...this.instances.values()].find(instance=>instance.active);}
    get voice(){return this.active?.voice;}
    frame(params,dt){for(const instance of this.instances.values())instance.frame?.(params,dt);}
    unlock(){for(const instance of this.instances.values())instance.unlock?.();}
    setMuted(value){for(const instance of this.instances.values())instance.setMuted?.(value);}
    dispose(){for(const instance of this.instances.values())instance.dispose?.();}
}
window.StageModuleHost=StageModuleHost;

class ModuleShelf {
    constructor(){
        this.root=document.getElementById('module-grid');this.dialog=document.getElementById('module-presentation-dialog');
        document.getElementById('module-presentation-close').onclick=()=>this.dialog.close();
        document.getElementById('module-presentation-form').onsubmit=event=>{event.preventDefault();this.save();};
        document.getElementById('module-expression-enabled').onchange=()=>this.expressionControls();
        document.getElementById('module-expression-strength').oninput=()=>this.expressionControls();
        this.refresh();
        window.addEventListener('studio-modules-changed',()=>this.refresh());
    }
    async refresh(){
        try{
            const manifest=await StudioModules.manifest();this.data=manifest;
            for(const spec of manifest.modules){
                let card=document.getElementById('module-card-'+spec.id);
                if(!card){
                    card=document.createElement('article');card.className='module-card';card.id='module-card-'+spec.id;
                    const icon=document.createElement('span');icon.className='module-icon';icon.textContent=spec.icon;
                    const title=document.createElement('h3');title.textContent=spec.title;
                    const description=document.createElement('p');description.textContent=spec.description;
                    const status=document.createElement('small');status.id='module-status-'+spec.id;status.textContent=spec.status;
                    const actions=document.createElement('div');actions.className='module-actions';
                    const open=document.createElement('button');open.type='button';open.className='soft-button';open.textContent='Open '+spec.title;open.disabled=true;
                    const look=document.createElement('button');look.type='button';look.className='text-button';look.textContent='Pose & face · 姿态表情';
                    look.onclick=()=>this.edit(spec.id);actions.append(open,look);card.append(icon,title,description,status,actions);this.root.append(card);
                    try{if(spec.controller)await StudioModules.script(spec.controller);
                        const instance=await StudioModules.controllers.get(spec.id)?.();
                        if(instance){StudioModules.instances.set(spec.id,instance);open.disabled=false;open.onclick=()=>instance.open();}
                    }catch{status.textContent='Module unavailable · 请刷新重试';}
                }
            }
            document.getElementById('module-character').textContent='Per character · 当前角色独立保存';
        }catch(error){document.getElementById('module-character').textContent=error.message;}
    }
    async edit(id){
        try{this.data=await StudioModules.manifest();this.editing=this.data.modules.find(module=>module.id===id);
            const spec=this.editing;document.getElementById('module-presentation-title').textContent=spec.title+' · Pose & face';
            const labels={auto:'Auto · 跟随内容',idle:'Still / idle · 待机',nod:'Gentle nod · 点头',think:'Thoughtful · 思考',shy:'Shy · 害羞',laugh:'Soft laugh · 轻笑',present:'Present · 介绍',neutral:'Neutral · 自然',happy:'Happy · 开心',thinking:'Thinking · 专注',surprised:'Surprised · 惊喜',singing:'Singing · 陶醉'};
            for(const [field,choices] of [['pose',spec.poses],['expression',spec.expressions]]){
                const select=document.getElementById('module-'+field);select.replaceChildren(...choices.map(value=>new Option(labels[value]||value,value)));select.value=spec.presentation[field];}
            document.getElementById('module-strength').value=spec.presentation.motion_strength;
            document.getElementById('module-expression-enabled').checked=spec.presentation.expression_enabled!==false;
            document.getElementById('module-expression-strength').value=spec.presentation.expression_strength??.55;
            document.getElementById('module-scene-preset').value=spec.presentation.scene_preset||'inherit';
            this.expressionControls();
            document.getElementById('module-presentation-status').textContent='仅保存当前角色在这个模块中的表演方式。';this.dialog.showModal();
        }catch(error){document.getElementById('module-character').textContent=error.message;}
    }
    expressionControls(){
        const enabled=document.getElementById('module-expression-enabled').checked;
        document.getElementById('module-expression').disabled=!enabled;
        const strength=document.getElementById('module-expression-strength');strength.disabled=!enabled;
        document.getElementById('module-expression-strength-value').value=Math.round(Number(strength.value)*100)+'%';
    }
    async save(){
        const status=document.getElementById('module-presentation-status');
        try{const response=await fetch('/api/modules/'+this.editing.id+'/presentation',{method:'POST',headers:{'Content-Type':'application/json','X-Studio-API':'1'},
            body:JSON.stringify({profile_id:this.data.active_profile,revision:this.data.revision,presentation:{pose:document.getElementById('module-pose').value,
                expression:document.getElementById('module-expression').value,motion_strength:Number(document.getElementById('module-strength').value),
                expression_enabled:document.getElementById('module-expression-enabled').checked,
                expression_strength:Number(document.getElementById('module-expression-strength').value),
                scene_preset:document.getElementById('module-scene-preset').value}})});
            const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'设置无效');
            this.data=result;status.textContent='Saved · 已保存到当前角色';
        }catch(error){status.textContent=error.message;}
    }
}
window.addEventListener('DOMContentLoaded',()=>{if(document.getElementById('module-grid'))window.moduleShelf=new ModuleShelf();});
