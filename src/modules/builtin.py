"""Registration is explicit: adding a module does not require server edits."""
from .core import ModuleSpec
from ..singing import SingingStudio, songs_directory
from ..singing_routes import router as singing_router
from ..story import StoryStudio,stories_directory
from ..story_routes import router as story_router


class ChatModule:
    def __init__(self, state):
        self.pipeline = state.pipeline

    async def start(self):
        await self.pipeline.start()

    async def close(self):
        await self.pipeline.stop()

    def module_status(self):
        return 'Waiting for stage' if self.pipeline.performance_active else 'Replying' if self.pipeline._is_replying else 'Ready'

    def default_presentation(self, profile):
        return {'pose': 'auto', 'expression': 'auto', 'motion_strength': profile.actions.intensity}


MODULES = (
    ModuleSpec('chat', 'Chat · 聊天', 'Live replies, viewer context and warm-up lines.', '✧', ChatModule,
               controller='/static/modules/chat-controls.js',
               defaults={'pose': 'auto', 'expression': 'auto', 'motion_strength': .35}),
    ModuleSpec('singing', 'Songs · 歌曲', 'Prepare a take once. Keep it in this character’s song library.', '♫',
               lambda state: SingingStudio(songs_directory(state.studio_path), state.pipeline),
               router=singing_router, controller='/static/singing-controls.js',
               output='/static/modules/singing-output.js',
               defaults={'pose': 'idle', 'expression': 'singing', 'motion_strength': .35},
               poses=('idle', 'nod', 'shy', 'laugh'), expressions=('singing', 'happy', 'shy', 'neutral')),
    ModuleSpec('story','Stories · 故事','Ten expressive stories. Prepare once, pick randomly, then return to chat.','☾',
               lambda state:StoryStudio(stories_directory(state.studio_path),state.pipeline,state),router=story_router,
               controller='/static/story-controls.js',output='/static/modules/story-output.js',
               defaults={'pose':'think','expression':'neutral','motion_strength':.25},
               poses=('idle','think','shy','present'),expressions=('neutral','happy','thinking','shy')),
)
