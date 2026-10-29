StudioModules.registerOutput('singing',async client=>{
    await StudioModules.script('/static/modules/performance-output.js');
    const adapter=await window.createPerformanceAdapter(client,'singing');
    let badge;
    const update=requests=>{
        if(!badge){
            badge=document.createElement('div');badge.className='song-request-badge';badge.hidden=true;
            badge.setAttribute('aria-live','polite');
            Object.assign(badge.style,{position:'fixed',top:'24px',right:'24px',zIndex:'12',maxWidth:'min(310px,35vw)',
                padding:'12px 16px',borderRadius:'16px',background:'rgba(28,22,45,.76)',color:'#f4ebff',
                border:'1px solid rgba(221,197,255,.25)',font:'500 14px/1.5 system-ui,sans-serif',
                backdropFilter:'blur(10px)',pointerEvents:'none',whiteSpace:'pre-line'});
            document.body.appendChild(badge);
        }
        const queue=requests?.queue||[];
        badge.hidden=!queue.length;
        badge.textContent=queue.length?`♫ 待唱 ${queue.length} 首\n下一首 · ${queue[0].title}`:'';
    };
    const handle=adapter.handle.bind(adapter),dispose=adapter.dispose.bind(adapter);
    adapter.handle=event=>{
        if(event.type==='song_requests_update')update(event.requests);
        else if(event.song_requests)update(event.song_requests);
        else if(event.type==='profile_changed')update(null);
        return handle(event);
    };
    adapter.dispose=()=>{badge?.remove();dispose();};
    return adapter;
});
