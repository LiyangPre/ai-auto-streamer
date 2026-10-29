// Standalone, silent visual rehearsal. No WebSocket, audio, provider or save API.
(() => {
    'use strict';
    const element=id=>document.getElementById(id),layer=element('singing-scene'),
        reduced=element('reduced'),media=matchMedia('(prefers-reduced-motion: reduce)');
    reduced.checked=media.matches;
    const scene=new SingingScene(layer,{preview:true,reducedMotion:reduced.checked}),
        expressions=new CharacterExpressionController({intensity:.55,reducedMotion:reduced.checked});
    let sway=true,position=0,previous=0,disposed=false;
    const updateExpression=()=>{
        const name=element('emotion').value;
        scene.setSinging(true,name);
        expressions.setExpression(name,{hold:true,source:'preview',intensity:Number(element('strength').value)});
    };
    scene.setActive(true,{companions:true});updateExpression();
    element('emotion').addEventListener('change',updateExpression);
    element('strength').addEventListener('input',updateExpression);
    element('blink').addEventListener('click',()=>expressions.blink());
    element('face-detail').addEventListener('change',event=>element('preview-stage').classList.toggle('face-detail',event.target.checked));
    element('rhythm').addEventListener('click',()=>{sway=!sway;element('rhythm').setAttribute('aria-pressed',String(sway));});
    element('breath').addEventListener('change',event=>scene.setBreathing(event.target.checked));
    reduced.addEventListener('change',()=>{
        scene.reducedMotion=expressions.reducedMotion=reduced.checked;
        if(reduced.checked)scene.pose={head:0,mic:0,body:0,breath:0};
    });
    const frame=now=>{
        if(disposed)return;
        if(previous&&now-previous<1000/30){requestAnimationFrame(frame);return;}
        const dt=previous?Math.min(.1,(now-previous)/1000):0;previous=now;
        if(!document.hidden&&!element('freeze').checked){
            if(sway)position+=dt;
            const started=performance.now();
            const expression=expressions.sample(dt);
            if(element('hold-blink').checked)expression.blink=1;
            scene.render({expressionState:expression,mouthOpen:0,mouthRound:0},dt,false,
                {playing:sway,position,bpm:72,motionStrength:.45,voicePlaying:false});
            if(scene.ready)element('motion-status').textContent=`${element('emotion').selectedOptions[0].textContent} · ${sway?'Rhythm running':'Rhythm paused'} · ${(performance.now()-started).toFixed(1)} ms/render · No sound`;
        }
        requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
    window.addEventListener('pagehide',()=>{disposed=true;scene.dispose();expressions.dispose();});
})();
