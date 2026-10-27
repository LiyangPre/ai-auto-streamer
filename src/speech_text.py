"""Small local readback: preserve the viewer's words; never call another model."""
import re


def chat_readback(messages, limit=32):
    """Read the core of the latest selected chat, without names or boilerplate.

    This is a bounded extract, not a semantic summary. Keep negations and the
    original wording. A batch gets one short lead-in, not a second roll call.
    """
    if not messages:
        return ""
    text = messages[-1].content.strip()
    text = re.sub(r"https?://\S+|www\.\S+", "", text)
    text = re.sub(r"<[^>]*>|\[[^\]]{1,16}\]", "", text)
    text = re.sub(r"@[^\s，,:：。！？!?]+\s*", "", text)
    text = re.sub(r"[\x00-\x1f\x7f`*_#]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Only strip greeting/address prefixes when meaningful text follows.
    core = re.sub(r"^(?:(?:主播|姐姐|你好|您好|哈喽|请问|我想问一下|想问一下)[，,：:、\s]*)+", "", text)
    if len(core.strip("，。！？!? ")) >= 2:
        text = core
    text = text.strip("，,：:；; ")
    if not text:
        return ""
    if len(text) > limit:
        # Prefer a complete first clause to cutting through a word.
        clauses = re.split(r"[，,。！？!?；;]", text)
        if 4 <= len(clauses[0]) <= limit:
            text = clauses[0]
        else:
            return text[:limit].rstrip() + "… "
    if text[-1:] not in "。！？.!?":
        text += "？" if re.search(r"(?:吗|么|呢)$|为什么|怎么|什么|哪|是否|能不能", text) else "。"
    return text
