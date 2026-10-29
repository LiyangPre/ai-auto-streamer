"""Conservative local broadcast rules, not a semantic or legal classifier.

Rules operate on normalized text; explicit variants cover common evasions without
globally replacing Chinese homophones (which would corrupt ordinary names).
"""
import re


RULES = {
    'abuse': re.compile(
        r'傻[逼比b]|煞[逼比b]|[操艹肏][你妳尼]|[操草艹肏曹][你妳尼]?[妈嗎玛馬]|'
        r'[草曹][泥尼][马玛嗎]|你[妈媽]的|死全家|去死吧|贱[人货]|废物东西|滚[蛋开]|'
        r'(?<![a-z])sb(?![a-z])|nmsl|cnm|fuckyou|motherfucker', re.I),
    'sexual': re.compile(
        r'儿童色情|幼女色情|未成年.{0,8}(裸|性交易)|色情视频|黄色网站|'
        r'做爱|性交|口交|肛交|自慰|精液|阴茎|阴道|裸聊|约炮|援交|卖淫|嫖娼|'
        r'强奸|强姦|rape(?:me|her|him)|porn(?:hub|ography)?', re.I),
    'advertising': re.compile(
        r'加(?:我|一下|我的|个)?(?:微信|微[信芯]|薇信|v信|vx|qq|群)|'
        r'扫码(?:进|加|领|购)|私信我|进群领|点击链接|点击.{0,6}下单|返现|'
        r'刷单|博彩|网赌|赌博|代充|引流|https?://|www\.', re.I),
    'danger': re.compile(
        r'制造炸弹|自制炸弹|炸药配方|制作毒品|制毒教程|恐怖主义|'
        r'杀死(?:你|他|她|他们|她们)|我要杀|教.{0,6}自杀|自杀教程'),
    # This streamer deliberately avoids heated political campaigning topics.
    # The operator can adjust categories and extra blocked words in YAML.
    'sensitive_topics': re.compile(r'政变|颠覆政权|恐怖组织|种族清洗|竞选拉票|政治宣传|纳粹万岁'),
}

# URL/contact patterns need punctuation before compaction as well as after it.
RAW_ADVERTISING = re.compile(r'https?://|www\.|\b[a-z0-9-]+\.(?:com|cn|net|top|xyz)(?:/|\b)', re.I)


def category_match(raw, compact, categories):
    for category in categories:
        rule = RULES.get(category)
        if rule and rule.search(compact):
            return category
        if category == 'advertising' and RAW_ADVERTISING.search(raw):
            return category
    return None
