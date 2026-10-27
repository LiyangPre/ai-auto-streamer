"""Exclusive stage ownership for performances that temporarily pause chat."""
import asyncio


class StageActivity:
    def __init__(self):
        self.owner = None
        self.idle = asyncio.Event()
        self.idle.set()

    def acquire(self, module_id):
        if self.owner and self.owner != module_id:
            raise ValueError('Another module is using the stage')
        self.owner = module_id
        self.idle.clear()

    def release(self, module_id):
        if self.owner != module_id:
            return False
        self.owner = None
        self.idle.set()
        return True
