"""Action mapper: emotion / action name -> renderer-agnostic action params.

Each action carries:
  - numeric params (mouthOpen, eyeOpen, bodyAngleX, ...) shared by all renderers
  - sprite / sprite_motion: used by the 2D joint rig (or the single-image fallback)
  - expression / motion:    used by the Live2D renderer
  - emotion:                representative emotion for the UI indicator
"""
import logging
from typing import Dict, Any, Optional

from .config import ActionConfig
from .motion_catalog import DEMO_ACTIONS, demo_action

logger = logging.getLogger(__name__)


class ActionMapper:
    """Maps emotions and action names to renderer-agnostic action params"""

    def __init__(self, config: ActionConfig):
        self.config = config

        # Core actions, extended below with eight cute joint-rig poses.
        self.actions: Dict[str, Dict[str, Any]] = {
            "idle": {
                "sprite": "neutral", "sprite_motion": "breathe",
                "mouthOpen": 0.0, "mouthForm": 0.0, "eyeOpen": 1.0,
                "eyeBallX": 0.0, "eyeBallY": 0.0, "bodyAngleX": 0.0,
                "bodyAngleY": 0.0, "browY": 0.0,
                "expression": "normal", "motion": "idle", "emotion": "normal",
            },
            "talk": {
                "sprite": "talk", "sprite_motion": "talk",
                "mouthOpen": 0.7, "mouthForm": 0.0, "eyeOpen": 1.0,
                "eyeBallX": 0.0, "eyeBallY": 0.0, "bodyAngleX": 0.0,
                "bodyAngleY": 0.0, "browY": 0.1,
                "expression": "normal", "motion": "idle", "emotion": "normal",
            },
            "wave": {
                "sprite": "happy", "sprite_motion": "sway",
                "mouthOpen": 0.5, "mouthForm": 0.8, "eyeOpen": 1.2,
                "eyeBallX": 0.0, "eyeBallY": 0.0, "bodyAngleX": -4.0,
                "bodyAngleY": 0.0, "browY": 0.4,
                "expression": "happy", "motion": "happy", "emotion": "happy",
            },
            "nod": {
                "sprite": "neutral", "sprite_motion": "nod",
                "mouthOpen": 0.2, "mouthForm": 0.3, "eyeOpen": 0.9,
                "eyeBallX": 0.0, "eyeBallY": 0.0, "bodyAngleX": 0.0,
                "bodyAngleY": 4.0, "browY": 0.2,
                "expression": "normal", "motion": "idle", "emotion": "normal",
            },
            "think": {
                "sprite": "think", "sprite_motion": "tilt",
                "mouthOpen": 0.0, "mouthForm": 0.0, "eyeOpen": 0.9,
                "eyeBallX": 0.8, "eyeBallY": 0.5, "bodyAngleX": -3.0,
                "bodyAngleY": 0.0, "browY": 0.3,
                "expression": "thinking", "motion": "thinking", "emotion": "thinking",
            },
            "laugh": {
                "sprite": "happy", "sprite_motion": "bounce",
                "mouthOpen": 0.9, "mouthForm": 1.0, "eyeOpen": 1.2,
                "eyeBallX": 0.0, "eyeBallY": 0.0, "bodyAngleX": 0.0,
                "bodyAngleY": 0.0, "browY": 0.5,
                "expression": "happy", "motion": "happy", "emotion": "happy",
            },
            "cry": {
                "sprite": "sad", "sprite_motion": "slump",
                "mouthOpen": 0.1, "mouthForm": -0.5, "eyeOpen": 0.7,
                "eyeBallX": 0.0, "eyeBallY": -0.5, "bodyAngleX": 0.0,
                "bodyAngleY": -5.0, "browY": -0.5,
                "expression": "sad", "motion": "sad", "emotion": "sad",
            },
            "angry": {
                "sprite": "angry", "sprite_motion": "shake",
                "mouthOpen": 0.4, "mouthForm": -0.3, "eyeOpen": 0.8,
                "eyeBallX": 0.0, "eyeBallY": 0.0, "bodyAngleX": 5.0,
                "bodyAngleY": 0.0, "browY": -0.8,
                "expression": "angry", "motion": "angry", "emotion": "angry",
            },
            "surprise": {
                "sprite": "surprised", "sprite_motion": "pop",
                "mouthOpen": 1.0, "mouthForm": 0.3, "eyeOpen": 1.5,
                "eyeBallX": 0.0, "eyeBallY": 0.5, "bodyAngleX": 0.0,
                "bodyAngleY": 3.0, "browY": 1.0,
                "expression": "surprised", "motion": "surprised", "emotion": "surprised",
            },
            "bow": {
                "sprite": "bow", "sprite_motion": "lean",
                "mouthOpen": 0.1, "mouthForm": 0.2, "eyeOpen": 0.3,
                "eyeBallX": 0.0, "eyeBallY": -0.3, "bodyAngleX": 0.0,
                "bodyAngleY": 8.0, "browY": 0.2,
                "expression": "normal", "motion": "idle", "emotion": "normal",
            },
        }

        # Additional cutout-rig gestures share the existing event protocol.
        for name in ("shy", "heart", "cheer", "double_wave", "peek", "present", "stretch", "curtsy"):
            self.actions[name] = {**self.actions["idle"], "sprite_motion": name,
                                  "expression": "happy", "emotion": "happy"}

        # Only publish poses this front-view cutout can perform convincingly.
        self.actions = {name: self.actions[name] for name in DEMO_ACTIONS}
        for action in self.actions.values():
            action["mouthOpen"] = 0.0  # Only audible speech may open the mouth.

        # LLM emotion tag -> action name
        self.emotion_to_action: Dict[str, str] = {
            "normal": "idle",
            "thinking": "think",
            "happy": "laugh",
            "sad": "cry",
            "angry": "angry",
            "surprised": "surprise",
        }

    def get_action(self, emotion: str) -> Dict[str, Any]:
        """Get action params for an LLM emotion tag"""
        name = self.config.emotion_map.get(emotion, self.emotion_to_action.get(emotion, "idle")) if self.config.automatic else "idle"
        action = self.get_action_by_name(name)
        if emotion == "thinking" and self.config.automatic:
            action["loop"] = True
        return action

    def get_action_by_name(self, name: str) -> Dict[str, Any]:
        """Get action params by action name (falls back to idle)"""
        name = demo_action(name)
        action = self.actions.get(name, self.actions["idle"])
        logger.debug(f"Action '{name}' -> sprite {action['sprite']} / {action['sprite_motion']}")
        return {**action, "name": name if name in self.actions else "idle", "intensity": self.config.intensity}

    def get_all_action_names(self) -> list:
        return list(self.actions.keys())

    def get_lip_sync_params(self, audio_volume: float) -> Dict[str, float]:
        return {"mouthOpen": audio_volume, "mouthForm": 0.0}

    def get_idle_params(self) -> Dict[str, Any]:
        return self.actions["idle"].copy()

    def get_all_emotions(self) -> list:
        return list(self.emotion_to_action.keys())
