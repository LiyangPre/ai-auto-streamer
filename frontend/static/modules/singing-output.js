StudioModules.registerOutput('singing',async client=>{
    await StudioModules.script('/static/modules/performance-output.js');
    return window.createPerformanceAdapter(client,'singing');
});
