StudioModules.registerController('chat',()=>({open(){
    document.getElementById('composer-title').scrollIntoView({behavior:'smooth',block:'center'});
    document.getElementById('danmaku-input').focus({preventScroll:true});
}}));
