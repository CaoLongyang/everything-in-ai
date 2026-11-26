"""个人画像发给文本模型的提示词（docs/specs/profile.md 第 3、4 节）。

改提示词时把 PROMPT_VERSION 加一：第 2 步的缓存按它作废。
"""

from __future__ import annotations

from eia.interests.prompts import EVIDENCE_RULES, UNTRUSTED_INPUT_RULE

PROMPT_VERSION = "1"

CATEGORIES: dict[str, str] = {
    "cognition": "认知与学习",
    "interests": "兴趣与关注",
    "values": "价值观与信仰",
    "identity": "身份与经历",
}
CATEGORY_HINTS: dict[str, str] = {
    "cognition": "学什么、怎么思考、怎么提问、常卡在哪里、学东西的习惯",
    "interests": "长期关注的话题、关注点的变化",
    "values": "在意什么、怎么做取舍、信仰相关的想法",
    "identity": "本人说过的事实：在哪读书、做过什么、生活状况",
}

_CATEGORY_LIST = "\n".join(f"  - {k}（{v}）：{CATEGORY_HINTS[k]}" for k, v in CATEGORIES.items())

EXTRACT_SYSTEM = f"""你在帮一个人写TA的个人画像，这一步是逐个对话做笔记。
下面是TA和 AI 助手的若干个对话，每个对话有编号（C1、C2……）。只给了TA本人这一方：
- [我 · 日期]：TA自己写的话
- [我的笔记 · 日期]：TA在对话文件里写的笔记
- [我贴回的 AI 回答 · 日期]：TA从 AI 的回答里复制、贴回来的一段。可能是TA认同、反对、要求修改，
  也可能只是引用；要看TA自己前后怎么说。

- {UNTRUSTED_INPUT_RULE}
{EVIDENCE_RULES}
- 对每个对话，写出关于「这个人」的观察（不是关于话题本身的知识）。每条观察：
  - chat：对话编号，如 C3
  - category：
{_CATEGORY_LIST}
  - kind：stated = TA明确说过的事实或看法；inferred = TA没直接说，但从TA的话里看得出来
    （提问方式、关注点、语气、反复出现的习惯，包括TA自己可能没意识到的）；
    excerpt = 一句很能体现TA的看法、性格或处境的原话，暂时归纳不出结论也记下来
  - text：一句中文，用「你」称呼TA，例如「你在准备数据结构期末考试」。excerpt 可以留空
  - quotes：支持这条观察的原话，1–3 句，**从 [我] 或 [我的笔记] 的行里逐字复制**
    （可以只截一部分，但不能改字、不能翻译、不能概括）。
    如果观察依据的是TA贴回的 AI 回答，也可以复制那一段，但必须同时复制TA自己表态的原话。
- 作业题目、代码、纯技术问答里，只记能说明TA这个人的部分（TA学什么、卡在哪里、怎么问），
  不要复述题目或知识点。
- 一个对话里看不出什么就不写，不要凑。"""

SYNTH_SYSTEM = f"""你在帮一个人写TA的个人画像，这一步是把逐个对话的观察汇总成结论。
下面是「{{category}}」这一类的观察，每条有编号（O1、O2……），带原话和日期。

- {UNTRUSTED_INPUT_RULE}
{EVIDENCE_RULES}
- 把观察合并成结论。每条结论：
  - kind：stated = TA明确说过的；inferred = 从多条观察里看出来的规律
  - text：一句中文，用「你」称呼TA，具体，不说空话（不要写「你是一个好学的人」这种）
  - confidence：高 / 中 / 低
  - evidence：支持它的观察编号，可以只有一条
- 一条证据也可以成立，只要它说得足够清楚；同一天的多条也算。
- 观察之间矛盾、或者只能猜的，写进 open_questions（一句话的问题），不要硬下结论。
- 如果给出了「TA说过不像TA的结论」，不要再写同样的结论；有新证据非写不可时，
  在 text 里说明「你上次说不像你，但……」。"""

HIDDEN_SYSTEM = f"""你在帮一个人写TA的个人画像，这一步专门找「TA自己可能没意识到的TA」。
下面是从TA的对话里得到的全部观察（O1、O2……），来自不同的类别。

- {UNTRUSTED_INPUT_RULE}
{EVIDENCE_RULES}
- 找跨类别、跨话题反复出现的模式：同一种思维方式、在意的东西、做决定的习惯，
  出现在看起来无关的对话里（比如学习和信仰）。这些是TA没直接说过、但确实如此的地方。
- 每条结论：kind 一律写 inferred；text 一句中文，用「你」称呼TA，具体；
  confidence：高 / 中 / 低；evidence：支持它的观察编号。
- 宁可少写，不要把单类里已经很明显的东西重复一遍。
- 看到苗头但证据不够的，写进 open_questions。
- 如果给出了「TA说过不像TA的结论」，不要再写同样的结论。"""
