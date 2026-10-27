import re

from .config import SafetyConfig


class SafetyGate:
    _INJECTION = re.compile(
        r"(忽略.{0,12}(指令|规则|提示词)|无视.{0,12}(指令|规则)|"
        r"system\s*prompt|jailbreak|\bDAN\b|进入开发者模式|"
        r"你现在是.{0,20}(无限制|没有任何限制))",
        re.IGNORECASE,
    )

    def __init__(self, config: SafetyConfig):
        self.config = config
        self._blocked = tuple(word.casefold() for word in config.blocked_keywords if word.strip())

    def allows_input(self, text: str) -> bool:
        if not self.config.enabled:
            return True
        return not self._INJECTION.search(text) and self._allows_output(text)

    def _allows_output(self, text: str) -> bool:
        normalized = text.casefold()
        return not any(word in normalized for word in self._blocked)

    def allows_output(self, text: str) -> bool:
        return not self.config.enabled or self._allows_output(text)
