// Scene assets load behind an opaque intermission frame. This never schedules,
// delays, restarts or pauses audio: finite performances retain their own clock.
(() => {
    'use strict';
    const labels={car:'银龙座驾', 'singing-room':'星光歌房', palace:'星光宫殿',garden:'玫瑰花园',moonlit:'月光露台',custom:'新场景',transparent:'透明舞台'};
    const CANCELLED=Symbol('scene-cancelled');
    class SceneAssets {
        constructor({ImageClass=globalThis.Image,timeoutMs=10000,cacheLimit=16}={}){
            this.ImageClass=ImageClass;this.timeoutMs=timeoutMs;this.cacheLimit=Math.max(4,Math.min(32,cacheLimit));this.images=new Map();}
        image(path){
            // Only installed local art and validated local media enter here.
            if(typeof path!=='string'||!/^\/(?:static\/|media\/)[^?#]*$/.test(path)||path.includes('..')||path.includes('\\'))
                return Promise.reject(new Error('Invalid scene artwork'));
            if(this.images.has(path)){const cached=this.images.get(path);this.images.delete(path);this.images.set(path,cached);return cached;}
            const pending=new Promise((resolve,reject)=>{
                const image=new this.ImageClass();let settled=false;
                const done=error=>{if(settled)return;settled=true;clearTimeout(timer);image.onload=image.onerror=null;error?reject(error):resolve(image);};
                const timer=setTimeout(()=>done(new Error('Scene artwork timed out')),this.timeoutMs);
                image.decoding='async';
                image.onerror=()=>done(new Error('Scene artwork unavailable'));
                image.onload=async()=>{try{if(image.decode)await image.decode();done();}catch{done(new Error('Scene artwork decode failed'));}};
                image.src=path;
            });
            this.images.set(path,pending);
            while(this.images.size>this.cacheLimit)this.images.delete(this.images.keys().next().value);
            pending.catch(()=>{if(this.images.get(path)===pending)this.images.delete(path);});
            return pending;
        }
        async all(paths){return Promise.all([...new Set(paths)].map(path=>this.image(path)));}
    }

    function painted(){return new Promise(resolve=>{
        let settled=false;
        const done=()=>{if(!settled){settled=true;clearTimeout(timer);resolve();}};
        // Hidden tabs can suspend RAF; do not leave a stale frame on resume.
        const timer=setTimeout(done,120);
        if(typeof requestAnimationFrame==='function')requestAnimationFrame(()=>requestAnimationFrame(done));
        else done();
    });}

    class SceneTransition {
        constructor(layer,{reducedMotion=false,timeoutMs=12000,afterPaint=painted}={}){
            this.layer=layer;this.reducedMotion=reducedMotion;this.timeoutMs=timeoutMs;this.afterPaint=afterPaint;
            this.title=layer?.querySelector('[data-scene-title]');this.message=layer?.querySelector('[data-scene-message]');
            this.retryButton=layer?.querySelector('[data-scene-retry]');this.dismissButton=layer?.querySelector('[data-scene-dismiss]');
            this.sequence=0;this.key=null;this.pending=null;this.failed=null;this.disposed=false;
            this.retry=()=>{if(this.failed)this.change(this.failed);};
            this.dismiss=()=>{this.failed=null;this.hide();};
            this.retryButton?.addEventListener('click',this.retry);this.dismissButton?.addEventListener('click',this.dismiss);
            this.hide();
        }
        show(state,background){
            clearTimeout(this.fadeTimer);
            if(!this.layer)return;
            this.layer.hidden=false;this.layer.dataset.state=state;this.layer.setAttribute('aria-busy',String(state==='loading'));
            if(this.title)this.title.textContent=state==='error'?'还没准备好，再试一下？':'稍等，我换个地方陪你';
            if(this.message)this.message.textContent=state==='error'?'新场景暂时没能加载，先留在这里。':`正在准备${labels[background]||'新场景'}…`;
            if(this.retryButton)this.retryButton.hidden=state!=='error';
            if(this.dismissButton)this.dismissButton.hidden=state!=='error';
        }
        hide(){clearTimeout(this.fadeTimer);if(this.layer){this.layer.hidden=true;this.layer.dataset.state='idle';this.layer.setAttribute('aria-busy','false');}}
        change(options){
            if(this.disposed)return Promise.resolve({cancelled:true});
            const {key,background,prepare,commit,transparent=false}=options;
            if(this.pending?.key===key){this.pending.commit=commit;return this.pending.promise;}
            this.pending?.cancel();
            const generation=++this.sequence;this.failed=null;this.pending=null;clearTimeout(this.fadeTimer);
            // A same-scene settings update has no intermission. Cancelling a
            // pending change back to the visible scene is immediate as well.
            if(key===this.key){this.hide();commit();return Promise.resolve({ready:true});}
            const job={key,commit,options,generation};this.pending=job;
            if(transparent)this.hide();else this.show('loading',background);
            const current=()=>!this.disposed&&generation===this.sequence;
            let timeout,cancel;
            const cancelled=new Promise(resolve=>{cancel=resolve;});
            job.cancel=()=>{clearTimeout(timeout);cancel(CANCELLED);};
            job.promise=Promise.race([Promise.resolve().then(prepare),new Promise((_,reject)=>{
                timeout=setTimeout(()=>reject(new Error('Scene preparation timed out')),this.timeoutMs);
            }),cancelled]).then(async result=>{
                clearTimeout(timeout);if(result===CANCELLED||!current())return {cancelled:true};
                job.commit();this.key=key;
                await this.afterPaint();if(!current())return {cancelled:true};
                this.pending=null;
                if(transparent||this.reducedMotion)this.hide();
                else if(this.layer){this.layer.dataset.state='ready';this.layer.setAttribute('aria-busy','false');
                    this.fadeTimer=setTimeout(()=>{if(current())this.hide();},220);}
                return {ready:true};
            }).catch(()=>{
                clearTimeout(timeout);if(!current())return {cancelled:true};
                this.pending=null;this.failed={...job.options,commit:job.commit};
                // The old scene remains active. The error frame becomes a
                // small corner card, rather than hiding a running performance.
                if(transparent)this.hide();else this.show('error',background);
                return {ready:false};
            });
            return job.promise;
        }
        dispose(){this.disposed=true;this.sequence++;this.pending?.cancel();this.pending=null;this.failed=null;this.hide();
            this.retryButton?.removeEventListener('click',this.retry);this.dismissButton?.removeEventListener('click',this.dismiss);}
    }
    if(typeof window!=='undefined'){window.SceneTransition=SceneTransition;window.SceneAssets=SceneAssets;}
    if(typeof module!=='undefined'&&module.exports)module.exports={SceneTransition,SceneAssets,painted};
})();
