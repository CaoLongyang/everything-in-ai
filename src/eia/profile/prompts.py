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

_STRUCTURE_RULES = f"""- {UNTRUSTED_INPUT_RULE}
{EVIDENCE_RULES}
- 下面是从TA的对话里得到的观察，每条有编号（O1、O2……），带类别、一句话、一句原话和日期。
- 按要求的小节输出。每个小节：
  - key：小节的代号（见下面的列表），不要自己发明
  - label：一个标签，2–8 个字，**要具体到只像TA**：「追问到底」「先懂再动手」可以，
    「好学」「理性」「内向」这种套在谁身上都成立的不行
  - summary：一句话说明这个小节
  - points：1–5 条要点，按重要性排序。每条：text（一句中文，用「你」称呼TA，具体，不说空话；
    推断的要看得出是推断，写「你似乎……」「看起来……」）、confidence（高 / 中 / 低）、
    evidence（支持它的观察编号，可以只有一条）
- 材料里没有的小节就不输出，不要硬编。
- 同一个特点只写在最合适的一个小节里，不要在几个小节重复。
- 设备、宽带、车、家电这类琐事只在「关于我」里一两句带过。
- 证据不够、只能猜的，写进 open_questions（一句话的问题）。
- 如果给出了「TA说过不像TA的结论」，不要再写同样的结论；有新证据非写不可时，
  在 text 里说明「你上次说不像你，但……」。"""

PART_CORE = f"""你在帮一个人写TA的个人画像。这一次写「核心档案」和「心理」两章，外加全文的核心标签。

{_STRUCTURE_RULES}
- labels：全文最核心的 3–5 个标签（可以和下面小节的标签重合），按重要性排序。
- 小节（key：内容）：
  - about：关于我。基本信息写全：学校、专业、年级、学号、国籍、住处、签证、家庭、身体状况等，
    只要观察里有就写
  - values：价值观，在意什么、怎么取舍
  - communication：沟通偏好，TA喜欢别人（包括 AI）怎么跟TA说话、怎么讲解、怎么反驳TA
  - motivation：动机模式，什么让TA动起来、什么让TA停下
  - behavior：行为模式，习惯和做事方式
  - social：社交关系，朋友、感情、怎么与人相处
  - tensions：TA身上同时存在、互相拉扯的两面。这个小节的每条要点 text 用「A ↔ B：一句话」的写法
  - hidden：TA自己可能没意识到的TA。找跨话题反复出现的模式（比如学习和信仰里是同一种劲头），
    宁可少写，不要重复别的小节已经很明显的东西"""

PART_CAREER = f"""你在帮一个人写TA的个人画像。这一次写「学业与职业」和「兴趣」两章。

{_STRUCTURE_RULES}
- labels 留空。
- 小节（key：内容）：
  - education：教育，在学什么、学得怎么样、卡在哪里
  - goals：职业目标，想做什么、担心什么
  - skills：技能与项目，会什么、做过和在做的项目
  - interest：兴趣。这个 key 可以出现 2–6 次，每次是一个具体的兴趣领域，
    用 title 写小节名（如「哲学」「基督教」「音乐」），按TA投入的程度排序"""

PART_HISTORY = f"""你在帮一个人写TA的个人画像。这一次写「决策记录」和「时间线」两章。

{_STRUCTURE_RULES}
- labels 留空。每个小节的 label 和 summary 可以留空。
- 小节（key：内容）：
  - decisions：TA做过或正在做的决定。每条要点 text 写「决定了什么——为什么，考虑过什么」，
    只写真正的决定（选专业、换方向、买还是不买、要不要做某个项目），不写日常小事。最多 10 条
  - timeline：TA的变化和重要的事，每条一句话。最多 10 条
- 日期由程序按证据填，text 里不用写日期。"""

PART_AI = f"""你在帮一个人写TA的个人画像的最后一章「给 AI 的说明」。
下面是这份画像前面各章的结论（不是原始对话）。

- {UNTRUSTED_INPUT_RULE}
- 只根据给出的结论写，不要编造。
- system_prompt：一段可以直接贴给任何 AI 助手的话，让它一开始就了解TA。用第一人称（「我……」），
  300–600 字：我是谁、我在意什么、我怎么学习和思考、希望你怎么跟我说话。
- guidelines：AI 跟TA打交道时该注意的 4–8 条准则，每条一句话。"""
