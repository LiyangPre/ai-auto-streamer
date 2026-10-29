// Silent visual QA: no websocket, audio or configuration API is constructed.
(() => {
    'use strict';
    const element=id=>document.getElementById(id),settings={companions:true,car_motion:true,car_vibration:true,dim:0},
        reduced=element('preview-reduced'),frame=element('scene-transition');
    reduced.checked=matchMedia('(prefers-reduced-motion: reduce)').matches;
    const car=new CarScene(element('car-scene'),{preview:true,reducedMotion:reduced.checked}),
        singer=new SingingScene(element('singing-scene'),{preview:true,reducedMotion:reduced.checked}),
        transition=new SceneTransition(frame,{reducedMotion:reduced.checked}),assets=new SceneAssets();
    let current=null,clock=0,previous=0,disposed=false,release=null;
    const portraitReady=async portrait=>{
        if(portrait.ready)return;
        if(!portrait.loading)portrait.loading=portrait.load();
        await portrait.loading;if(!portrait.ready){portrait.loading=null;throw new Error('Portrait unavailable');}
    };
    const choose=target=>{
        release?.();release=null;
        let fail=element('fail-next').checked;element('fail-next').checked=false;
        const gate=element('hold-ready').checked?new Promise(resolve=>{release=resolve;}):Promise.resolve();
        transition.change({key:target,background:target,prepare:async()=>{
            const tasks=[gate];
            if(target==='car')tasks.push(portraitReady(car),assets.all(['/static/scenes/car/astra-car-v1.png']));
            else if(target==='singing-room')tasks.push(portraitReady(singer),assets.all(['/static/scenes/singing/music-room-v2.png','/static/scenes/singing/happy-seated-dolls-v1.png']));
            else tasks.push(assets.image('/static/scenes/royal-palace.png'));
            await Promise.all(tasks);if(fail){fail=false;throw new Error('Requested preview failure');}
        },commit:()=>{
            current=target;car.setActive(target==='car',settings);singer.setActive(target==='singing-room',settings);
            singer.setSinging(target==='singing-room');
            element('preview-backdrop').hidden=target!=='palace';
            if(target==='palace')element('preview-backdrop').src='/static/scenes/royal-palace.png';
            for(const button of document.querySelectorAll('[data-target]'))button.setAttribute('aria-pressed',String(button.dataset.target===target));
        }});
    };
    for(const button of document.querySelectorAll('[data-target]'))button.addEventListener('click',()=>choose(button.dataset.target));
    element('release-ready').addEventListener('click',()=>{release?.();release=null;});
    reduced.addEventListener('change',()=>{
        car.reducedMotion=singer.reducedMotion=transition.reducedMotion=reduced.checked;
        if(car.drive)car.drive.reducedMotion=reduced.checked;
    });
    const render=now=>{
        if(disposed)return;if(previous&&now-previous<1000/30){requestAnimationFrame(render);return;}
        const dt=previous?Math.min(.1,(now-previous)/1000):0;previous=now;
        if(!document.hidden){clock+=dt;car.render({mouthOpen:0},dt,false);
            singer.render({mouthOpen:0},dt,false,{playing:current==='singing-room',voicePlaying:false,position:clock,bpm:72,motionStrength:.45});
            element('preview-state').textContent=`Scene: ${current||'preparing'} · Frame: ${frame.dataset.state} · Silent preview`;
        }
        requestAnimationFrame(render);
    };
    choose('car');requestAnimationFrame(render);
    window.addEventListener('pagehide',()=>{disposed=true;release?.();transition.dispose();car.dispose();singer.dispose();});
    window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
})();
