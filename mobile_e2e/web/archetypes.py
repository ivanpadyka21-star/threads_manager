"""A curated library of proven viral post archetypes for Threads.

Derived from real high-performing posts in the account's niche (relationships /
sexuality / women's audience). The single most important lesson encoded here:
on Threads the growth engine is the **reply chain** ("Ланцюжок") — a post that
makes strangers answer or argue in the comments gets amplified far more than a
polished monologue. So most archetypes are engineered to *pull a reply*.

Pure data + small helpers, no LLM calls — fully unit-testable. The strategist
agent reads these (``get_viral_formats``) to plan a varied set, and they can be
seeded as saved prompts for one-click use in the Studio.
"""

from __future__ import annotations

from typing import List, Optional

# Each archetype:
#   id         — stable slug
#   name       — {ru, en} short human label
#   audience   — who it addresses (men / women / both)
#   engine     — {ru, en} WHY it goes viral (the mechanic to preserve)
#   guidance   — {ru, en} how to write it (for the writer model)
#   examples   — 1-3 real skeletons (in the account's languages) to learn from
#   metric     — the metric it primarily drives (replies | views | shares)
ARCHETYPES = [
    {
        "id": "chain_question_men",
        "name": {"ru": "Вопрос-исповедь к мужчинам", "en": "Confession question to men"},
        "audience": "men",
        "metric": "replies",
        "engine": {
            "ru": "Прямой провокационный вопрос к ОДНОМУ полу на табу-тему + «тільки чесно» "
                  "заставляет мужчин исповедоваться в комментах. Цепочка ответов = алгоритм разгоняет.",
            "en": "A direct, provocative question to ONE gender on a taboo topic + 'be honest' "
                  "makes men confess in the comments. The reply chain gets amplified.",
        },
        "guidance": {
            "ru": "Коротко (одна-две строки). Обратись «Чоловіки/Мужчины,…», задай острый личный "
                  "вопрос (измены, желание, что цепляет, чего боятся) и добавь «тільки чесно». "
                  "Без нравоучений — только вопрос, который хочется прокомментировать.",
            "en": "Short (1–2 lines). Open with 'Men,…', ask one sharp personal question "
                  "(cheating, desire, what hooks them, fears) and add 'be honest'. Just the question.",
        },
        "examples": [
            "Чоловіки, зраджували колись? Якщо так — чому? Тільки чесно.",
            "Мужчины, что вас цепляет в женщине сильнее внешности? Только честно.",
            "Чоловіки, про що ви думаєте, але ніколи не скажете вголос? 😉",
        ],
    },
    {
        "id": "chain_question_women",
        "name": {"ru": "Дилемма/вопрос к женщинам", "en": "Dilemma question to women"},
        "audience": "women",
        "metric": "replies",
        "engine": {
            "ru": "Релейтна дилемма про отношения/первый шаг заставляет женщин делиться опытом. "
                  "Каждая узнаёт себя → комментит.",
            "en": "A relatable relationship/first-move dilemma makes women share their take.",
        },
        "guidance": {
            "ru": "Коротко. «Дівчата/Девочки,…» + дилемма без единственно правильного ответа "
                  "(первый шаг, писать первой, простить или нет). Провоцируй мнение, а не согласие.",
            "en": "Short. 'Girls,…' + a dilemma with no single right answer to spark opinions.",
        },
        "examples": [
            "Норм чи стрьомно дівчині робити перший крок? 👀",
            "Девочки, простили бы измену ради семьи? Честно.",
            "Дівчата, написали б першою тому, хто мовчить три дні?",
        ],
    },
    {
        "id": "debate_bait",
        "name": {"ru": "Дебат-вопрос (спор)", "en": "Debate bait"},
        "audience": "both",
        "metric": "replies",
        "engine": {
            "ru": "Спорное утверждение/вопрос, где нет консенсуса → люди аргументируют друг с "
                  "другом. Комментов больше, чем лайков — алгоритм это обожает.",
            "en": "A divisive claim/question with no consensus → people argue with each other.",
        },
        "guidance": {
            "ru": "Задай вопрос, который делит аудиторию пополам (по чому видно освіченість, "
                  "деньги vs чувства, дружба м/ж). Можно адресовать одному полу про другой.",
            "en": "Ask something that splits the room in half; can address one gender about the other.",
        },
        "examples": [
            "Чоловіки, по чому ви розумієте, що дівчина освічена?",
            "Существует ли дружба между мужчиной и женщиной? Аргументируйте.",
            "Що важливіше в парі — пристрасть чи стабільність? Тільки без брехні.",
        ],
    },
    {
        "id": "participatory",
        "name": {"ru": "Соучастие («з кожного по…»)", "en": "Participatory prompt"},
        "audience": "both",
        "metric": "replies",
        "engine": {
            "ru": "Низкий порог входа: каждому предлагают что-то ВЫЛОЖИТЬ (фото/факт/эмодзи). "
                  "Люди с радостью участвуют → лавина комментов. Один из самых охватных форматов.",
            "en": "Low barrier: everyone is invited to POST something (photo/fact/emoji). Avalanche of replies.",
        },
        "guidance": {
            "ru": "Дай простое задание «з кожного по…»: фото, где нравишься себе; одно слово о "
                  "своём кризисе; песня под настроение. Тепло, без осуждения, легко ответить.",
            "en": "Give a simple 'everyone drop a…' task: a photo you love yourself in, one word, a song.",
        },
        "examples": [
            "З кожного по фото, де ви собі ну дууууже подобаєтесь 💫",
            "З кожного по одній пісні, під яку ви згадуєте когось 🎧",
            "Одним словом: чого вам зараз найбільше не вистачає?",
        ],
    },
    {
        "id": "sexual_health_hack",
        "name": {"ru": "Польза-шок (сексздоровье)", "en": "Value-shock (sexual health)"},
        "audience": "both",
        "metric": "shares",
        "engine": {
            "ru": "Реально полезный совет по сексуальному здоровью, поданный дерзко и на эмоциях "
                  "(капс, эмодзи, «виагра на минималках») → сохраняют и репостят. Экспертность + шок.",
            "en": "A genuinely useful sexual-health tip delivered boldly (caps, emoji) → saves and shares.",
        },
        "guidance": {
            "ru": "Как СММ-сексолог дай один конкретный лайфхак (добавки, техника, миф) — коротко, "
                  "дерзко, с эмоцией, но БЕЗ медицинских обещаний-гарантий и без графики секса. "
                  "Заканчивай мини-объяснением «почему работает».",
            "en": "As a sexologist give one concrete tip — bold, brief, no medical guarantees, no explicit acts.",
        },
        "examples": [
            "Дівчата, аргінін + цинк — і партнер скаже вам дякую 🙈 Пояснюю чому нижче…",
            "Хочете більше чутливості? Почніть з дихання, а не з іграшок. Серйозно.",
        ],
    },
    {
        "id": "relatable_meme",
        "name": {"ru": "Эмодзи-мем (релейт)", "en": "Emoji meme (relatable)"},
        "audience": "women",
        "metric": "views",
        "engine": {
            "ru": "Узнаваемая женская правда через эмодзи-контраст (овуляція 🔥 vs лютеїнова фаза 😱). "
                  "Мгновенный релейт → лайки, репосты «это про меня».",
            "en": "A recognisable truth via emoji contrast → instant relatability, likes and shares.",
        },
        "guidance": {
            "ru": "Очень коротко. Контраст двух состояний через эмодзи (фазы цикла, до/после, "
                  "он пишет/он молчит). Юмор и самоирония, без объяснений.",
            "en": "Very short. Contrast two states with emoji; humour and self-irony, no explanation.",
        },
        "examples": [
            "Овуляція: 🔥💃💋\nЛютеїнова фаза: 🥴😳😱",
            "Він написав: 😌✨\nВін онлайн і мовчить: 🫠🔪",
        ],
    },
    {
        "id": "longing_intimacy",
        "name": {"ru": "Тоска по близости", "en": "Longing / intimacy"},
        "audience": "both",
        "metric": "views",
        "engine": {
            "ru": "Уязвимое признание о желании близости/нежности бьёт в одиночество аудитории. "
                  "Это ТВОЙ доказанный формат (пост про «обійми» — 4170 просмотров).",
            "en": "A vulnerable confession about wanting closeness hits the audience's loneliness.",
        },
        "guidance": {
            "ru": "Средняя длина (2–4 строки), от первого лица, тепло и чувственно, с лёгкой "
                  "пошлостью-намёком в конце. Заверши так, чтобы захотелось ответить «я теж».",
            "en": "Medium length, first person, warm and sensual with a light suggestive hint; invite 'me too'.",
        },
        "examples": [
            "Хтось теж відчуває, що катастрофічно не вистачає… обіймів? 😉 Ні, не тільки обіймів…",
            "Іноді так хочеться, щоб хтось просто був поруч. І щоб руки… ну, ви зрозуміли 😜",
        ],
    },
]

_BY_ID = {a["id"]: a for a in ARCHETYPES}


def _L(value, lang: str) -> str:
    if isinstance(value, dict):
        return value.get(lang) or value.get("ru") or next(iter(value.values()), "")
    return str(value)


def list_archetypes(lang: str = "ru") -> List[dict]:
    """Return the archetypes flattened for a language (for the agent tool / UI)."""
    lang = "en" if lang == "en" else "ru"
    return [
        {
            "id": a["id"], "name": _L(a["name"], lang), "audience": a["audience"],
            "metric": a["metric"], "engine": _L(a["engine"], lang),
            "guidance": _L(a["guidance"], lang), "examples": list(a["examples"]),
        }
        for a in ARCHETYPES
    ]


def get_archetype(archetype_id: str, lang: str = "ru") -> Optional[dict]:
    a = _BY_ID.get(archetype_id)
    if not a:
        return None
    return next(x for x in list_archetypes(lang) if x["id"] == archetype_id)


def viral_formats_text(lang: str = "ru") -> str:
    """A compact menu of proven formats for a strategist/writer prompt."""
    lang = "en" if lang == "en" else "ru"
    header = (
        "PROVEN VIRAL FORMATS on Threads (the growth engine is the REPLY CHAIN — "
        "posts that make strangers answer/argue get amplified; comments matter "
        "more than likes). Pick and VARY these; adapt to the persona, do not copy:"
        if lang == "en" else
        "ЗАЛЁТНЫЕ ФОРМАТЫ в Threads (двигатель охвата — ЦЕПОЧКА КОММЕНТОВ: посты, "
        "которые заставляют незнакомцев отвечать/спорить, разгоняются; комментарии "
        "важнее лайков). Выбирай и ЧЕРЕДУЙ, адаптируй под персону, не копируй дословно:"
    )
    lines = [header]
    for a in list_archetypes(lang):
        lines.append(
            f"- {a['name']} [{a['audience']}, → {a['metric']}]: {a['engine']} "
            f"Напр.: «{a['examples'][0]}»" if lang != "en"
            else f"- {a['name']} [{a['audience']}, → {a['metric']}]: {a['engine']} "
                 f"e.g. «{a['examples'][0]}»"
        )
    return "\n".join(lines)


def seed_prompts(store, lang: str = "ru") -> int:
    """Create a saved prompt per archetype (skip ones already present by name).

    Returns the number of prompts newly created.
    """
    existing = {p["name"] for p in store.list_prompts()}
    created = 0
    for a in list_archetypes(lang):
        name = f"🔥 {a['name']}"
        if name in existing:
            continue
        text = (
            f"{a['guidance']}\n\nМеханика: {a['engine']}\nПример: {a['examples'][0]}"
            if lang != "en" else
            f"{a['guidance']}\n\nEngine: {a['engine']}\nExample: {a['examples'][0]}"
        )
        store.add_prompt(name, text)
        created += 1
    return created
