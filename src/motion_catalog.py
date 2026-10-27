"""Gestures supported by the front-facing demo artwork."""
DEMO_ACTIONS = ("idle", "nod", "think", "shy", "laugh", "present")
LEGACY_ACTIONS = {"talk": "idle", "wave": "present", "cry": "think", "angry": "think",
                  "surprise": "nod", "bow": "nod", "heart": "shy", "cheer": "laugh",
                  "double_wave": "present", "peek": "shy", "stretch": "idle", "curtsy": "nod"}


def demo_action(name):
    return LEGACY_ACTIONS.get(name, name)
