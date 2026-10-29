"""AI Auto Streamer - AI-controlled virtual human livestreaming system"""
__version__ = "0.1.0"
__all__ = ["Config", "Pipeline"]


def __getattr__(name):
    # Offline utilities can import pure helpers without loading the entire live
    # server, its provider SDKs, and audio backends into a separate environment.
    if name == 'Config':
        from .config import Config
        globals()[name] = Config
        return Config
    if name == 'Pipeline':
        from .pipeline import Pipeline
        globals()[name] = Pipeline
        return Pipeline
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
