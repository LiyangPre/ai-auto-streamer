"""Small, explicit GLM chat catalog; prices are CNY per million tokens.

Checked against BigModel's pricing/model docs on 2026-10-28. Unknown commercial
model sizes stay unknown; open-weight family sizes are not assigned to aliases.
"""
import re


PRICING_URL = 'https://docs.bigmodel.cn/cn/guide/start/pricing'
DEFAULT_GLM_MODEL = 'glm-4.7-flash'
# FlashX is deliberately last while the user's 4.7-FlashX service is busy.
DEFAULT_GLM_FALLBACKS = [
    'glm-4-flash-250414', 'glm-4-flashx-250414', 'glm-4-air-250414',
    'glm-4.5-air', 'glm-4.7-flashx',
]
GLM_CHAT_MODELS = [
    dict(id='glm-4.7-flash', input_price=0, output_price=0, total_b=30, active_b=3, context='200K'),
    dict(id='glm-4-flash-250414', input_price=0, output_price=0, total_b=None, active_b=None, context='128K'),
    dict(id='glm-4-flashx-250414', input_price=0.1, output_price=0.1, total_b=None, active_b=None, context='128K'),
    dict(id='glm-4-air-250414', input_price=0.5, output_price=0.5, total_b=None, active_b=None, context='128K'),
    dict(id='glm-4.7-flashx', input_price=0.5, output_price=3, total_b=None, active_b=None, context='200K'),
    dict(id='glm-4.5-air', input_price=0.8, output_price=2, total_b=106, active_b=12, context='128K'),
]


def validate_model_ids(values):
    if len(values) > 7:
        raise ValueError('Use at most seven fallback models')
    result = []
    for value in values:
        if not isinstance(value, str) or not re.fullmatch(r'[\w./:-]{1,100}', value):
            raise ValueError('Invalid fallback model ID')
        value = value.lower()
        if value not in result:
            result.append(value)
    return result


def validate_glm_no_thinking(model):
    value = model.lower()
    if not value.startswith('glm-') or value.startswith(('glm-z1', 'glm-5.3', 'glm-4.1v-thinking')):
        raise ValueError('Choose a GLM text model that supports thinking disabled')
    if value == 'glm-4.5-flash':
        raise ValueError('GLM-4.5-Flash redirects to GLM-4.7-Flash; use the current model ID')
    return model
