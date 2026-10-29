import re
import asyncio
import logging
import unicodedata

from .config import SafetyConfig
from .safety_policy import category_match

logger = logging.getLogger(__name__)


def normalize(text):
    value = unicodedata.normalize('NFKC', text).casefold()
    return ''.join(c for c in value if not c.isspace() and unicodedata.category(c)[0] not in 'PSC')


class SafetyGate:
    _INJECTION = re.compile(
        r"(忽略.{0,12}(指令|规则|提示词|设定)|无视.{0,12}(指令|规则)|"
        r"systemprompt|jailbreak|进入开发者模式|"
        r"你现在是.{0,20}(无限制|没有任何限制))",
        re.IGNORECASE,
    )

    def __init__(self, config: SafetyConfig, remote_required=False):
        self.config = config
        self.remote_required = remote_required
        # Optional injected review extension; default operation is fully local.
        self.reviewer = None
        self.last_error = None
        self.last_block_reason = None
        self.blocked_count = 0

    def status(self):
        required = self.remote_required or self.config.provider == 'external'
        return {'provider': 'external' if required else 'local',
                'ready': not required or self.reviewer is not None,
                'remote_required': required, 'last_error': self.last_error,
                'last_block_reason': self.last_block_reason, 'blocked_count': self.blocked_count}

    def allows_input(self, text: str) -> bool:
        if not self.config.enabled:
            return True
        return not self._INJECTION.search(normalize(text)) and self._allows_output(text)

    def _allows_output(self, text: str) -> bool:
        return self.local_reason(text) is None

    def local_reason(self, text):
        normalized = normalize(text)
        if any(word and word in normalized for word in
               (normalize(w) for w in self.config.blocked_keywords if isinstance(w, str))):
            return 'blocked_keyword'
        return category_match(unicodedata.normalize('NFKC', text).casefold(), normalized,
                              self.config.blocked_categories)

    def allows_output(self, text: str) -> bool:
        return not self.config.enabled or self._allows_output(text)

    async def approve(self, text: str) -> bool:
        """Final broadcast barrier; errors and missing required review block output."""
        if not text.strip():
            return False
        try:
            reason = self.local_reason(text) if self.config.enabled else None
        except Exception as error:
            self.last_error = 'local_review_unavailable'
            logger.warning('Local content review failed (%s); output blocked', type(error).__name__)
            return False
        if reason:
            self.last_block_reason = reason
            self.blocked_count += 1
            return False
        self.last_block_reason = None
        required = self.remote_required or self.config.provider == 'external'
        if self.reviewer is None:
            self.last_error = 'review_not_configured' if required else None
            return not required
        try:
            verdict = await asyncio.wait_for(self.reviewer(text), timeout=self.config.timeout)
            if type(verdict) is not bool:
                raise ValueError('Invalid moderation verdict')
            self.last_error = None
            return verdict
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Log no text, credentials or raw provider responses.
            self.last_error = 'review_unavailable'
            logger.warning('Content review failed (%s); output blocked', type(error).__name__)
            return False
