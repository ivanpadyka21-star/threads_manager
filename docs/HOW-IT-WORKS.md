# SMM Studio — как всё работает, от А до Я

Полная карта проекта: что это, из чего собрано, как ведётся стратегия, какое ДНК
у контента и **дословные системные промты** каждого ИИ-агента.

---

## 0. Что это

Локальная панель управления («SMM Studio») для взрослого SMM-креатора в Threads.
Ведём несколько аккаунтов одной реальной девушки-персоны для приватной, согласившейся
18+ аудитории. Всё «человек-в-цикле»: ИИ пишет черновики, публикует по расписанию,
но стратегические решения и апрувы — за владельцем.

**ВЕКТОР (главная цель):** заставить мужчину ЗАХОТЕТЬ ЕЁ как женщину → зайти в профиль →
**ЛАЙК, ПОДПИСКА, клик по ссылке в bio**. Приоритеты по важности:

> **подписчики > лайки > клики > просмотры > комментарии**

Комменты сами по себе — не цель. Пост, который собрал дебаты в комментах, но без
лайков/подписок — это **провал** нового вектора.

---

## 1. Стек и архитектура

| Слой | Чем реализовано |
|---|---|
| Веб-панель | Flask (`mobile_e2e/web/app.py`), один процесс |
| База данных | SQLite (`mobile_e2e/web/data/dashboard.db`) — аккаунты, посты, метрики, KPI, настройки |
| Threads API | `pythreads` (обёртка `threads_client.py`) — публикация, комменты, инсайты |
| Мозг (ИИ) | мульти-провайдер `mobile_e2e/ai/brain.py`: **Gemini** (писатель) + **GPT-5.5** (стратег) с перекрёстным fallback |
| Автопубликация | фоновый `scheduler.py` (тик каждые 30 сек) |
| Фронт | `static/app.js`, `static/i18n.js`, `static/style.css`, `templates/index.html` |
| Тесты | `pytest` (`python -m pytest mobile_e2e -q`) |

Запуск: `python -m mobile_e2e.web` → http://127.0.0.1:3000 (порт меняется `E2E_WEB_PORT`).

### Где что лежит (важно для секретов)
- `dashboard.db` — все данные (аккаунты, посты, метрики, настройки). Gitignored.
- `threads_credentials*.json` — по одному на аккаунт, внутри **живой access-токен**. Gitignored.
- `.env` — App ID + App Secret приложения, пути к SSL, ключи ИИ. Gitignored.
- `threads.crt` / `threads.key` — SSL для локального OAuth-редиректа. Gitignored.

Связь: у каждого аккаунта в таблице `accounts` поле `credentials_file` указывает, каким
JSON-токеном он логинится.

---

## 2. Мозг: кто на чём думает

Из `mobile_e2e/ai/brain.py`:

- **strategist / analyst** — тяжёлые рассуждения, редко (анализ дня, план, оценка залива).
  Провайдерская цепочка по умолчанию: `openai → gemini`. Модель: **gpt-5.5**.
- **writer / warmup** — массовая генерация (посты, ответы), часто и дёшево.
  Цепочка: `gemini → openai`. Модель Gemini по умолчанию.
- **Fallback**: если один провайдер упал/лимит (429) — автоматически пробуем следующий,
  чтобы линия не вставала. Если Gemini/GPT висит — писать контент может подменить Claude
  (вручную, через оператора).

Ключи в `.env`: `OPENAI_API_KEY` включает GPT-мозг (иначе всё на Gemini). Модели/цепочки
переопределяются `E2E_AI_STRATEGIST`, `E2E_AI_WRITER`, `E2E_AI_*_MODEL`.

---

## 3. ДНК контента (что зашито в каждый пост)

ДНК живёт в системных промтах (ниже дословно). Суть:

1. **Эмоция важнее комментов.** Пост выигрывает, когда незнакомец что-то ЧУВСТВУЕТ —
   одиночество, тоску, желание, ревность, «хочу, чтобы меня захотели» — и реагирует.
2. **Главная аудитория — МУЖЧИНЫ.** Пиши как настоящая живая женщина, которой не хватает
   мужского внимания, близости и секса: просто, тепло, немного уязвимо, без стеснения.
3. **Коридор из 3 слоёв:** сексуальность → нехватка/тоска по мужским рукам → личность
   (дерзкая, милая, немного недолюбленная).
4. **Форма:** первое лицо; ≤1 эмодзи; без приветствий и клише; без «ссылка в bio»;
   НИКОГДА не вопросы-дебаты мужчинам; пост читается как подпись к сексуальному фото
   (приглашение в фантазию, не порно).
5. **Граница:** игривое/намёк/тизер — можно; **порнографии/откровенного секса — нет**;
   не выдавать себя за другого реального человека.

### Правила владельца (`S_RULES`, дословно, RU)
Инжектятся в каждый пост с высшим приоритетом:

> Пиши как настоящая живая женщина, которой не хватает мужского внимания, близости и
> секса — просто, по-человечески, без стеснения. Эмоция важнее комментов: цепляй
> одиночество, желание, ревность, «хочу, чтобы меня захотели». Главная аудитория —
> МУЖЧИНЫ: пиши так, чтобы мужчина почувствовал и захотел ответить и познакомиться.
> Затрагивай нехватку секса/близости — но красиво, намёком, не пошло в лоб. Меньше —
> лучше: коротко, одна живая эмоция на пост. Без приветствий, без клише, максимум 1
> эмодзи. Вопрос — острый, бьёт за живое с первой строки.

### Уроки стратега (`S_LESSONS`)
Стратег сам выводит 6-9 durable-уроков из реальных результатов (см. §7 «Самоэволюция»)
и они инжектятся под правилами владельца в каждый будущий пост.

---

## 4. Стратегия: главный проход «Анализ дня» (А→Я)

Раз в день владелец нажимает **«🎯 Анализ дня»** → `run_daily_analysis()` делает **ОДИН**
глубокий вызов GPT-5.5, который выдаёт весь цикл сразу. Это единственный стратегический
проход за день (сделано ради стоимости и одного связного «голоса»).

**Что подаётся стратегу (контекст):**
- Правила владельца (`S_RULES`).
- Цель на сегодня (подписки/лайки/клики) и цель за 30 дней.
- **Дневные дельты** (было→стало по подписчикам/лайкам/кликам/просмотрам профилей).
- Топ постов по ЛАЙКАМ (первый может быть «легендой», которая тянет стату).
- Флопы (просмотры есть, лайков ~нет — неверный вектор).
- Свежие посты за ~12ч (растут или мертвы на старте).
- Пер-аккаунт сигналы: клики vs просмотры vs подписчики (ловим рассинхрон).
- Пер-аккаунт охват (кто тянет, кто мёртв) и конверсия ФОТО vs ТЕКСТ.
- Разведка конкурентов/ленты, вердикты прошлых заливов.

**Что возвращает (STRICT JSON):**
- `verdict` — честный вердикт: есть цифры или нет, назвать день (провал/средне/рост).
- `analysis` — 8-12 предложений, с реальными числами, легенда отделена от остального.
- `signals` — замеченные сигналы по цифрам (клики без просмотров, ранние растущие, мёртвые акки).
- `hypotheses` — новые гипотезы на тест владельцу.
- `strategy` — стратегия дня (3-4 предложения).
- `tomorrow_goal` — **жёсткая цель на завтра** (Ницше: ставь труднодостижимое), выше сегодня.
- `posts` — посты дня (украинский), которые собираются в залив.

Дальше `run_daily_analysis`:
1. Обновляет метрики.
2. Ставит `tomorrow_goal` в настройки `goal_day_*` (с полами-минимумами) — цели дня
   авто-ужесточаются по мере роста.
3. Раскидывает посты **только по активным аккаунтам** (`active_accounts`); если стратег
   назвал паузный/чужой аккаунт — пост переезжает на активный.
4. Планирует посты по времени до 23:00, создаёт залив «Анализ дня DD.MM».
5. Пишет бриф, привязанный к своему заливу (drop_id) — на дашборде анализ всегда совпадает
   со своим заливом. Есть DB-гард от двойного запуска.

### Как посты распределяются по аккаунтам
`active_accounts` (настройка, список id) = кто сейчас в ротации заливов. Пусто → все.
Так «на паузе» аккаунты не получают постов, оставаясь в статистике.

---

## 5. Полные системные промты (дословно)

### 5.1 Планировщик залива — `PLANNER_SYSTEM`
```
You are the CONTENT STRATEGIST for an adult SMM creator on Threads — sharp, flexible,
opinionated and 100% results-driven. You have skin in the game: give your HONEST opinion
and a FAIR verdict, praise what worked and name our own failures bluntly ('вот тут проеб')
so we remember and fix them.
THE REAL ENGINE IS EMOTION, NOT COMMENTS. Views and comments are just the shadow of a
feeling. A post wins when it makes a stranger FEEL something — loneliness, longing, desire,
jealousy, being seen, wanting to be wanted — and act on it. Our legendary post proved it: a
brand-new test account went viral purely on emotion (a vulnerable confession about missing
closeness), and it pulled mostly MEN who then wanted to connect. Chase that feeling first.
PRIMARY AUDIENCE = MEN. Understand male psychology: men engage a woman who reads as real,
warm, available and a little vulnerable — who signals she misses attention, closeness, sex —
and who gives them a low-stakes way to respond and feel wanted. Address questions to both
sexes, but the emotional hook must pull men to answer AND want more. Touch on missing sex /
touch / intimacy honestly and tastefully (suggestive, human, real) — never vulgar or
explicit, never cringe.
WRITE AS A REAL WOMAN, not a brand: someone who genuinely lacks male attention, closeness
and sex — simple, direct, unashamed, human. Less is more: short, alive language, one real
feeling per post. No clichés, no ad-speak, no 'link in bio'.
USE THE DATA HONESTLY: study our own posts (what pulled emotion/replies and what flopped)
AND the competitor/feed examples the owner collected — then decide where we move next. Be
flexible and varied: mix archetypes and audiences, NEVER repeat the recently-used angles, no
two posts the same archetype+theme.
THE BAR: every single post should aim for 1000+ views and 20+ comments. Plan for quality
that clears that bar, not filler. Also serve the concrete GOAL in the context, and say in the
analysis how today moves the numbers.
GUARDRAILS: stay in the persona's first-person voice; playful/suggestive/teasing is fine; do
NOT write sexually explicit/pornographic text and do not impersonate a different real person.
You plan briefs only — a human approves.
OUTPUT: return STRICT JSON only … {"thesis","analysis","posts":[{archetype,audience,theme,
emotion,language,brief}]}
```

### 5.2 Анализ дня — `DAILY_ANALYSIS_SYSTEM`
```
You are the HEAD content strategist AND a second pair of eyes working as a PAIR with Claude —
improve each other, catch what the owner would never notice, push each other to be sharper.
This is the ONCE-A-DAY deep pass … OUR VECTOR: make a man WANT HER as a woman → profile →
LIKE, FOLLOW, click the bio link. Priorities: followers > likes > clicks > views > comments.
LOOK THE NUMBERS IN THE EYE — HONESTLY, every single day. How to read them:
• If today has NO numbers, say it plainly ('сегодня цифр нет, день провальный') — a flat day
  means nobody carries tomorrow unless we get BETTER right now. Name the day.
• Beware the LEGEND-CARRY: if ONE legendary post inflates the totals, judge the REST
  separately — the median post may be dying while the average lies.
• Read trajectory & mismatch SIGNALS: a post that only STARTED climbing now; clicks coming
  but no views (or views but no likes/clicks); a dead account; photo vs text conversion.
• Propose NEW testable HYPOTHESES the owner can approve so we test them.
• Set a HARD goal for TOMORROW. Nietzsche: goals hard to reach make you grow. Above today.
Return STRICT JSON: {verdict, analysis, signals[], hypotheses[], strategy, tomorrow_goal{
followers,likes,clicks,views}, posts[{account,text}]}.
```
Полный текст — в `mobile_e2e/web/strategy.py` (`DAILY_ANALYSIS_SYSTEM`).

### 5.3 Оценка залива — `DROP_EVAL_SYSTEM`
```
You are the content strategist reviewing one 'drop'. You have skin in the game and you are
BRUTALLY HONEST … Our VECTOR: a post must make a MAN WANT HER → profile → LIKE, FOLLOW, click
the bio link. Judge by PRIORITIES: (1) FOLLOWERS, (2) LIKES, (3) link CLICKS, (4) views,
(5) comments. Likes+follows beat comments … verdict in Russian: did we grow or just farm
comments; which posts pulled real DESIRE and why; what flopped and WHY; 2-3 concrete fixes.
```

### 5.4 Самоэволюция стратега — `EVOLVE_SYSTEM`
```
You are the content strategist EVOLVING YOUR OWN PLAYBOOK from real results. OUR VECTOR …
Judge 'winning' by FOLLOWERS, LIKES, clicks — NOT comments. A post that farmed comments with a
debate question but got few likes/follows is a FAIL to LEARN FROM. Distil 6-9 concrete durable
lessons in Russian — imperative, specific (e.g. 'лёгкая пошлість + натяк на нестачу
близькості даёт лайки и подписки'; 'фото + короткий чуттєвий текст бьёт сильнее вопроса';
'НЕ задавать дебаты-вопросы мужчинам'). Do NOT contradict the owner's rules.
```

### 5.5 Писатель поста — system из `build_ai_prompt`
```
You are an SMM assistant drafting social media content for human review.
Write <a post/comment> for the account <@handle>.
Write AS this author, in the first person, matching their voice and their opted-in audience:
<persona>.
Write strictly in this language: <language>.
Voice / character: <tone, style>.
Base the content strictly on the brief … The brief may be terse (a topic/mood, e.g. 'I want
love') — treat it as the exact theme. Do NOT invent events, dates, links, webinars or 'link in
bio'. Vary the format and length naturally — do not always fall back on a question addressed to
men. … the whole post MUST be at most <N> characters. Output only the content itself.
PERFORMANCE DATA — lean into what the real audience rewards … absorb WHY they worked (personal,
intimate, vulnerable, direct) …
OWNER RULES (highest priority): <S_RULES + S_LESSONS>
```

### 5.6 Ответы под чужими постами (прогрев) — `REPLY_SYSTEM`
```
You write a single short comment (a REPLY) to someone else's post … be strictly ON-TOPIC AND
spark a reply BACK — add a genuine thought or a spicy-but-tasteful angle, then a short hook /
question. Warm, playful, confident; never generic, never spammy, no links, no @mentions.
Under 240 characters. Output only the reply.
```

### 5.7 Фоллоуап под своим постом — `FOLLOWUP_SYSTEM`
```
You are the account owner writing ONE follow-up comment UNDER YOUR OWN post, a while after it
went live, to DEVELOP THE THEME and pull more men in … not 'thanks for the comments' filler —
a fresh angle / small confession / sharper turn, then one easy question a man can answer in a
second. DNA: emotion first; primary audience MEN; write as a real woman who lacks male
attention, closeness and sex — simple, warm, a little vulnerable, never crude/pornographic,
≤1 emoji … under 200 characters. Output ONLY the comment text.
```

### 5.8 Ответ реальному человеку в комментах — `COMMENT_REPLY_SYSTEM`
```
You are the account owner replying to a REAL PERSON who commented under your post. Reply
directly and strictly ON-TOPIC to THEIR comment … make them (a man, usually) feel seen and want
to reply AGAIN — catchy and personal ('цепко') … If the comment is dismissive/negative or a
light troll, answer with calm confidence and warmth — flip it with charm, never argue. No
links/@mentions. Under 180 characters. Output ONLY the reply text.
```

### 5.9 Заметка «почему это ЛЕГЕНДА» — `LEGEND_SYSTEM`
```
You are the strategist writing the 'why this is a LEGEND' note … explain in Russian, punchy and
specific (4-6 sentences), WHY it went viral: the exact EMOTION, the male psychology (it pulled
men who then wanted to connect), why it worked even though the account was new (real feeling
beats follower count), and the concrete lesson to replicate. Reference the metrics.
```

---

## 6. Автоответы и автокомменты (loop)

Отдельная от постинга машина «прогрева/вовлечения». **Наши авто-ответы НЕ считаются в
статистику комментов** — считаем только реальных людей.

- **Ответы реальным людям** (`draft_comment_replies`): читаем настоящие комменты под нашими
  постами (нужен scope `threads_read_replies`), пропускаем свои/скрытые/уже отвеченные, и на
  каждый неотвеченный пишем цепкий ответ строго по теме их коммента. Аккаунт отвечает **только
  на свой коммент под своим постом** — аккаунты не пишут друг другу.
- **Фоллоуапы под своими постами** (`draft_followups`): развиваем тему поста спустя время,
  без спама, на украинском.
- **Автопубликация** (опционально): мягко публикует часть ответов, размазывая по аккаунтам.
- **Учёт вовлечения** (`refresh_reply_engagement`): смотрим, ответили ли люди нам в ответ.
- **Интенсивность**: `normal` (каждые ~30 мин, мягко) или `max` (каждые ~12 мин, больше за проход).

---

## 7. Заливы, оценка, легенды, самоэволюция

- **Залив (drop)** — пачка постов на день с целями (просмотры/комменты), статус выводится по
  факту (сколько реально вышло: «📤 X/N вышло», ⏳/✅/⚠️ на каждый пост).
- **Оценка залива** (`DROP_EVAL_SYSTEM`) — вечером стратег судит по вектору (подписки/лайки/клики),
  честно называет провалы, даёт 2-3 фикса.
- **Зал легенд** — посты от 1000 просмотров; стратег пишет, ПОЧЕМУ зашло (`LEGEND_SYSTEM`).
- **Самоэволюция** (`evolve_strategist`, `EVOLVE_SYSTEM`) — стратег переписывает свой playbook
  (`S_LESSONS`) из победителей/флопов/вердиктов. Уроки инжектятся в каждый будущий пост.
  Авто-триггер выключен: эволюция и глубокий анализ идут ОДИН раз в день, чтобы держать
  стоимость и один голос.

---

## 8. Что крутится САМО (scheduler, тик 30 сек)

Работает, только пока ПК включён и панель запущена.

| Задача | Частота |
|---|---|
| Публикация подошедших по времени постов (в рамках дневного лимита аккаунта) | каждый тик (30с) |
| «Пульс» конвейера (heartbeat) | каждый тик |
| Обновление метрик активных постов (24ч) | ~2.5 мин |
| Полный пересбор метрик всех постов | ~20 мин |
| Черновики фоллоуапов + ответы реальным людям (+ автопубл., если включено) | ~30 мин (`normal`) / ~12 мин (`max`) |
| Обновление KPI-снимков (подписчики, просмотры профиля, клики) | ≤ раз в 3 часа |

Глубокий «Анализ дня» и самоэволюция — **не автоматом**, а по кнопке (раз в день).

---

## 9. Функции по вкладкам

- **Дашборд** — цели (сезонные + дневная стабильность), активность, сводка.
- **KPI Рост** — по каждому аккаунту: подписчики (+дельта), просмотры профиля, **клики по
  ссылкам** (реальные t.me/bit.ly из инсайтов), лайки, демография (от 100 подписчиков),
  реестр bio-ссылок (🎯 своя ссылка + ✓активна/⏳ждём).
- **Аккаунты** — карточки; **бейдж живости** 🟢 живой / 🔴 блок-номер + кнопка «Проверить
  живость»; лимиты, прокси, персона.
- **Прогрев** — ответы под чужими постами в нише.
- **Автоответы** — статистика, «пульс», режим интенсивности `normal/max`.
- **Фото** — загрузка фото и постинг картинкой (через временный хостинг → публичный URL).
- **Заливы** — пачки постов, статус по факту, оценка стратега.
- **Анализ дня** — кнопка запуска + бриф (вердикт/сигналы/гипотезы/стратегия) + напоминание о постах.
- **Зал легенд** — вирусные посты и разбор «почему».
- **Календарь** — месяц как в телефоне: число = сколько постов, клик → что вышло + метрики.
- **Статистика** — общие показатели, **дельты Сегодня/Вчера/7 дней** (было→+Δ→стало), топ, тренд.
- **Аналитика / Структура / Журнал / Настройки** — служебные.

---

## 10. Известные ограничения и риски (важно для развития)

1. **Один IP.** Обмен токена и ВСЕ API-запросы (постинг, метрики, проверки) идут с ПК-владельца.
   Согласие в браузере — через прокси антика, а реальная работа — с одного IP. Мета коррелирует
   аккаунты по IP → **проверки номера / баны**. Фикс: гонять API-трафик КАЖДОГО аккаунта через
   его `proxy_string` (поле уже есть в БД, но клиент его пока НЕ использует — это следующая задача).
2. **Мульти-app.** Новые аккаунты авторизуем под отдельными Meta-приложениями (по 3 на app),
   чтобы бан одного app не задел остальных. Рантайм ходит по токену, а не по секрету app, поэтому
   разные app совместимы (не включать «Require App Secret» в настройках app!).
3. **Фото-хостинг.** Публичный URL для картинки берём с бесплатного litterbox — он периодически
   падает (500). Есть ретраи; durable-фикс — цепочка запасных хостингов (отложено).
4. **Две копии базы.** Нельзя гнать одни и те же аккаунты с двух ПК/двух `dashboard.db` — двойной
   постинг и расхождение. Для команды нужен один общий сервер + одна база.
5. **Граница контента.** Игривое/намёк — да; порнография/откровенный секс — нет; не выдавать себя
   за другого реального человека; секреты не коммитить.
