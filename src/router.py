"""意图路由：规则层命中封闭意图，未命中走 default。

直播间弹幕是高度重复的封闭集合（问候/告别/问设定/夸夸/送礼/提问），
规则层 <1ms 即可覆盖大部分流量，不需要 embedding 或小 LLM 分类。

因无复杂推理场景，所有路由统一低 max_tokens + 关思考，
路由的作用是分派话术风格与"是否模板直出"，而非选择模型档位。
"""
import logging
import random
import re
from dataclasses import dataclass
from typing import List, Optional, Protocol

from .config import RouteConfig, RouterConfig


class RouteMessage(Protocol):
    content: str
    msg_type: str

logger = logging.getLogger(__name__)

GREETING = "greeting"
FAREWELL = "farewell"
PERSONA = "persona"
PRAISE = "praise"
QUESTION = "question"
GIFT = "gift"
ENTER = "enter"
DEFAULT = "default"

# 顺序即优先级：persona 必须在 question 之前，否则"你是谁"会被疑问词规则截走
_PATTERNS: List[tuple] = [
    (PERSONA, re.compile(
        r"(你是谁|你叫什么|你的名字|你叫啥|几岁|多大了|你多大|你是男|你是女|"
        r"你是ai|你是AI|你是人吗|是机器人|虚拟主播|介绍一下你|你在干嘛|你在干什么|"
        r"你能做什么|你会什么|你怕什么|你喜欢什么|你的声音)"
    )),
    (GREETING, re.compile(
        r"(?:^|[\s,，。.!！?？~～])(你好|您好|哈喽|哈罗|嗨|hello|hi|hey|在吗|在不在|"
        r"早上好|中午好|下午好|晚上好|早安|午安|主播好|好呀)(?=$|[\s,，。.!！?？~～]|呀|啊|嘛|么)",
        re.IGNORECASE
    )),
    (FAREWELL, re.compile(
        r"(再见|拜拜|掰掰|(?<!\d)88(?:6)?(?!\d)|走了|下线|退了|困了|睡了|下次见|回见)"
    )),
    (PRAISE, re.compile(
        r"(好看|漂亮|可爱|厉害|牛\b|牛逼|太强|好强|赞|棒棒|好棒|真棒|(?<!\d)666+(?!\d)|"
        r"爱你|喜欢你|好喜欢|帅|好美|真美|好听|声音好听|卡哇伊|kawaii|awsl)"
    )),
    (QUESTION, re.compile(
        r"(为什么|怎么会|如何|什么|啥|哪里|哪个|哪些|谁|多少|能不能|可不可以|"
        r"是不是|要不要|吗\s*$|吗[？?。~～\s]*$|呢\s*$|[？?]\s*$)"
    )),
]


@dataclass
class RouteDecision:
    intent: str
    route: RouteConfig
    # 命中模板时直接可播的文本，None 表示需要走 LLM
    template_text: Optional[str] = None
    # 用于日志与前端观测
    matched: str = ""

    @property
    def is_template(self) -> bool:
        return self.template_text is not None


class IntentRouter:
    def __init__(self, config: RouterConfig):
        self.config = config

    def _route_config(self, intent: str) -> RouteConfig:
        routes = self.config.routes
        if intent in routes:
            return routes[intent]
        return routes.get(DEFAULT, RouteConfig())

    def classify(self, content: str) -> tuple:
        """返回 (intent, matched_keyword)"""
        text = content.strip()
        if not text:
            return DEFAULT, ""

        for intent, pattern in _PATTERNS:
            m = pattern.search(text)
            if m:
                return intent, m.group(0)

        return DEFAULT, ""

    def route_for(self, intent: str) -> RouteConfig:
        return self._route_config(intent)

    def route(self, msg: RouteMessage) -> RouteDecision:
        if not self.config.enabled:
            return RouteDecision(intent=DEFAULT, route=self._route_config(DEFAULT))

        if msg.msg_type == "gift":
            return RouteDecision(intent=GIFT, route=self._route_config(GIFT))
        if msg.msg_type == "enter":
            return RouteDecision(intent=ENTER, route=self._route_config(ENTER))

        intent, matched = self.classify(msg.content)
        route = self._route_config(intent)

        if not route.enabled:
            return RouteDecision(intent=intent, route=route, matched=matched)

        template_text = self._pick_template(msg.content, route)
        return RouteDecision(
            intent=intent, route=route, template_text=template_text, matched=matched
        )

    def _pick_template(self, content: str, route: RouteConfig) -> Optional[str]:
        """模板直出条件：路由开启 + 有模板 + 弹幕足够短（长句更可能需要真回复）"""
        if not route.use_template or not route.templates:
            return None
        if len(content.strip()) > self.config.template_max_length:
            return None
        return random.choice(route.templates)

    def fill_template(self, template: str, user_name: str = "", **extra) -> str:
        """安全填充模板占位符，缺字段不抛异常"""
        values = {"user": user_name, **extra}
        try:
            return template.format(**values)
        except (KeyError, IndexError):
            return template

    def route_batch(self, messages: List[RouteMessage]) -> RouteDecision:
        """窗口内多条弹幕聚合时使用的路由。

        只要有一条命中封闭意图就按该意图回（问候/夸夸优先），
        否则走 default，由 LLM 一次性回应多人以提升互动密度。
        """
        if not messages:
            return RouteDecision(intent=DEFAULT, route=self._route_config(DEFAULT))
        if len(messages) == 1:
            return self.route(messages[0])

        priority = [GREETING, PRAISE, PERSONA, FAREWELL, QUESTION]
        hits = {}
        for msg in messages:
            intent, matched = self.classify(msg.content)
            if intent != DEFAULT:
                hits.setdefault(intent, matched)

        for intent in priority:
            if intent in hits:
                route = self._route_config(intent)
                # 聚合场景不模板直出：一句话要覆盖多个人
                return RouteDecision(intent=intent, route=route, matched=hits[intent])

        return RouteDecision(intent=DEFAULT, route=self._route_config(DEFAULT))

    @staticmethod
    def all_intents() -> List[str]:
        return [intent for intent, _ in _PATTERNS] + [GIFT, ENTER, DEFAULT]
