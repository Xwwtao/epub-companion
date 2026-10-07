"""所有发给 LLM 的 prompt。系统提示一律由 system_prompt() 拼出，保证都带名著兜底条款。

只放章节标题和正文，不放书名、作者等元数据（spec §2）。
"""

import json

# spec §2.1：名著兜底条款，所有生成请求共用
SPOILER_GUARD = """\
【防剧透规则，必须严格遵守】
- 你可能认出这部作品，但必须当作第一次读：只使用本次提供的文本和摘要，不得使用你对这部作品、其续作、改编作品或相关评论的任何已有知识。
- 不得提及或暗示作品名、作者、系列名。
- 不得预告、暗示或评价后续发展，禁用"后来""最终""伏笔""这将导致"等指向未来的说法。
- 人物的身份、关系、真名只按已提供文本揭示的程度描述；文本中还是化名、谜团或未揭晓的，摘要中也保持原样。
- 不得补充文本中没有出现的人物、地点、事件。
- 不确定某个信息是否出现在提供的文本中时，宁可省略。"""

WRITING_RULES = """\
【写作要求】
- 一律用中文写，即使原文是英文。
- 只有原文本身是英文时，人名、地名第一次出现才在中文译名后附原文，如"伊丽莎白（Elizabeth）"；原文是中文（包括译本）时不要补充任何外文或罗马字拼写。
- 人物称谓沿用【人物名单】中的统一称谓；如果文本揭示了某人的新名字或新身份，按文本如实记录。"""

SUMMARY_JSON_FORMAT = """\
【输出格式】
只输出一个 JSON 对象，不要输出其他内容：
{"summary": "主要情节，最多 150 字", "ending_state": "结束时的状态：所在场景、人物处境、悬而未决的事，最多 60 字", "names": [{"name": "统一称谓", "aliases": ["别名、昵称"], "original": "原文中出现的英文原名，否则留空"}]}
summary 只保留推动故事的主要情节：发生了什么、谁做了什么、有什么结果。省略具体的时间、数字、物品、次要人物和过程细节，不要按段落顺序逐一复述。
篇幅随原文长短而定，原文很短就写得很短，不要为了凑字数补充或推测。
names 只列文本中出场或被提到的有名字的人物，只写文本里出现过的信息。
aliases 只放专指这个人的名字、昵称、绰号；不要放"女孩""老板""死者""刑警"这类泛称或身份描述。"""

ROLE = "你是一位读书伴侣，帮读者回顾他已经读过的内容。"

TASK_CHAPTER = "任务：为【本章原文】写摘要。【最近几章摘要】和【人物名单】只是上下文，不要复述它们。"
TASK_CHUNK = "任务：本章太长，被分成了几段。只为【本段原文】写摘要，ending_state 写本段结束时的状态。【最近几章摘要】和【人物名单】只是上下文，不要复述它们。"
TASK_MERGE = "任务：把同一章各段的摘要按顺序合并成整章的摘要。summary 覆盖整章，ending_state 取最后一段结束时的状态，names 合并去重。"


def system_prompt(task: str, output_format: str) -> str:
    return "\n\n".join([ROLE, task, SPOILER_GUARD, WRITING_RULES, output_format])


def chapter_label(parent_titles: list[str], title: str) -> str:
    return " · ".join([*parent_titles, title])


def _context_blocks(recent: list[tuple[str, str]], roster: list[dict]) -> list[str]:
    blocks = []
    if roster:
        blocks.append("【人物名单】\n" + "\n".join(_roster_line(p) for p in roster))
    if recent:
        blocks.append("【最近几章摘要】\n" + "\n".join(f"- {label}：{summary}" for label, summary in recent))
    return blocks


def _roster_line(person: dict) -> str:
    extras = []
    if person.get("original"):
        extras.append(person["original"])
    if person.get("aliases"):
        extras.append("又称 " + "、".join(person["aliases"]))
    return f"- {person['name']}" + (f"（{'；'.join(extras)}）" if extras else "")


def chapter_messages(label: str, text: str, recent, roster) -> list[dict]:
    user = "\n\n".join([*_context_blocks(recent, roster), f"【本章】{label}\n【本章原文】\n{text}"])
    return [
        {"role": "system", "content": system_prompt(TASK_CHAPTER, SUMMARY_JSON_FORMAT)},
        {"role": "user", "content": user},
    ]


def chunk_messages(label: str, text: str, index: int, total: int, recent, roster) -> list[dict]:
    user = "\n\n".join(
        [*_context_blocks(recent, roster), f"【本章】{label}（第 {index}/{total} 段）\n【本段原文】\n{text}"]
    )
    return [
        {"role": "system", "content": system_prompt(TASK_CHUNK, SUMMARY_JSON_FORMAT)},
        {"role": "user", "content": user},
    ]


def merge_messages(label: str, parts: list[dict], roster) -> list[dict]:
    parts_text = "\n".join(
        f"第 {i} 段：{json.dumps(part, ensure_ascii=False)}" for i, part in enumerate(parts, start=1)
    )
    user = "\n\n".join([*_context_blocks([], roster), f"【本章】{label}\n【各段摘要】\n{parts_text}"])
    return [
        {"role": "system", "content": system_prompt(TASK_MERGE, SUMMARY_JSON_FORMAT)},
        {"role": "user", "content": user},
    ]


TASK_RECAP = """\
任务：根据【已读章节摘要】，为读者写一段整体的前情回顾，帮他在继续读之前快速想起故事。
- 读者已经读过这些内容，你只是提醒，不是复述。像一位读过同样内容的朋友，用几句话帮他找回印象。
- 只保留继续往下读必须记得的东西：主要人物是谁、彼此什么关系；核心事件和关键转折；现在悬而未决的问题。
- 次要人物、具体时间地点、数字、调查过程中的来回推敲一律省略。
- 绝对不要逐章复述，不要一章一段。把多章内容归并成故事的几个阶段，写成 2~4 段。最后一段落在已读内容结束时的局面上。"""

TASK_RECAP_GROUP = """\
任务：这是已读内容中连续的一部分章节。根据【已读章节摘要】，写一段这部分的情节概要，供之后汇总成整体回顾。
- 保留主线、关键转折和主要人物的处境变化，省略次要细节。"""

RECAP_JSON_FORMAT = """\
【输出格式】
只输出一个 JSON 对象，不要输出其他内容：
{"recap": "回顾正文，段落之间用换行分隔"}
篇幅随已读内容多少而定，最多 500 字；已读内容很少就写得很短。"""


def recap_messages(items: list[tuple[str, str]], group: bool = False) -> list[dict]:
    """items 为 (章节标签, 摘要) 列表；group=True 时为分组汇总的中间步骤。"""
    body = "\n".join(f"- {label}：{summary}" for label, summary in items)
    return [
        {"role": "system", "content": system_prompt(TASK_RECAP_GROUP if group else TASK_RECAP, RECAP_JSON_FORMAT)},
        {"role": "user", "content": f"【已读章节摘要】\n{body}"},
    ]
