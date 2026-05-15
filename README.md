# Препоръчваща система за книги

Уеб приложение и API за персонализирани **Top‑N** препоръки по заданието: **регистрация/вход**, **cold start** (жанрове, език, любими автори, бързи Like / Dislike / «Чел(а) съм»), hybrid модел, социален сигнал, обяснения, корици.

## Съответствие с методичката по препоръчващи системи

Спрямо презентацията „Проекти по препоръчващи системи“ проектът покрива очакваната логика: **дефиниран проблем и хипотеза**, **реализирано решение с демо**, **анализ чрез offline метрики** и текстова база за отчет (виж таб **Оценка** в UI и разделите там).

## Възможности

- **Акаунти**: регистрация, вход с парола (**bcrypt**), сесия в HTTP cookie (`recsys_session`), плюс **кратка анкета при запис** (темпо на четене, настроение, формат) — подсилва cold start в CBF.
- **Cold start**: след регистрация или вход без история се отваря **модален мини-квиз** (не таб): явни предпочитания (жанр, език, автори) и бърз implicit feedback към каталога → запис в `interactions`.
- **Каталог от мрежата**: **[Open Library](https://openlibrary.org)** Search API (без ключ) — импорт/обновяване през UI (**Каталог**) или CLI `python -m book_recsys.fetch_catalog "…"`.
- **Приятели и hybrid**: при взаимни приятелства моделът добавя **социален сигнал** (харесвания, прочетени, високи оценки) към CF+CBF+популярност; обясненията включват „**Приятел:** …“.
- ...оригинално от заданието: **За теб** (hybrid), **каталог**, **популярни**, **библиотека**, **оценка** (offline метрики), **MMR** разнообразие, **обяснения** (жанр, автор, подобна книга, приятели, анкета).

## Корици и каталог

- Книгите се зареждат от **`data/books.csv`** (разделител **`;`**, за да няма конфликт със запетаи в описанието).
- Колони: `title;authors;description;tags;language;year;isbn;cover_url`
- Ако **`cover_url`** е празно, системата генерира линк към **Open Library**:  
  `https://covers.openlibrary.org/b/isbn/{ISBN}-L.jpg`
- Ако корицата не се зареди в браузъра, UI ползва `static/placeholder-cover.svg`.

### Как да зареждаме / разширяваме данните за книгите

1. **Ръчно или таблично (препоръчително за курсов проект)**  
   Редактирай `data/books.csv` (UTF‑8). Добави ISBN и при нужда директен `cover_url`. После импорт:

   ```bash
   python -m book_recsys.import_csv          # добавя към празна или съществуваща БД*
   python -m book_recsys.import_csv --replace  # ИЗТРИВА всички книги и interactions, после импорт
   ```

   *Без `--replace` импортът **слива** по ISBN или заглавие+автор (новите полета допълват съществуващите записи). `--replace` изчиства всички книги и interactions — ползвай го само ако това е целта.*

2. **Open Library без ключ (вградено)**  
   - От таб **Каталог**: поле за търсене и **Импорт** (изисква вход). Повтарящи се ISBN или същото заглавие+автор се **обновяват** с по-богати метаданни, където е възможно.  
   - От терминал (нужен интернет):

     ```bash
     python -m book_recsys.fetch_catalog --limit 35 "historical fiction"
     python -m book_recsys.fetch_catalog --limit 30 --descriptions 'subject:"Detective and mystery stories"'
     ```

   - В production можеш да зададеш **`CATALOG_ADMIN_TOKEN`** в `.env`; тогава UI показва поле за token и заявките изискват заглавка `X-Catalog-Token`.

3. **Google Books API** (с API key) — алтернатива за богати описания; може да допълва CSV → `import_csv`.

4. **Изкуствен интелект (по желание)**  
   LLM може да предложи **кратко резюме**, **тегове** или превод на описание от суров текст — винаги **проверявай** и пази източника (риск от „галюцинации“). В този проект **няма задължителна LLM интеграция**; можеш да зададеш `OPENAI_API_KEY` за бъдещи скриптове или offline обогатяване. За курсов проект обикновено са достатъчни Open Library + CSV.

## Стартиране

Python 3.11+.

```bash
cd Book_recommendation_system   # или пълният път към хранилището
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # по желание; виж book_recsys/config.py за променливи
python -m book_recsys
```

По подразбиране `SESSION_SECRET` идва от `.env` или dev fallback в `config.py`. За **production** задай `ENV=production`, силен `SESSION_SECRET` (мин. 32 символа), `DEBUG=0`, при HTTPS и `SESSION_SECURE_COOKIES=1`. Зад reverse proxy задай `TRUSTED_HOSTS` на реалното host име (и при нужда `127.0.0.1` за healthcheck).

Отвори **http://127.0.0.1:8000**. Ако `SEED_DEMO_DATA=1` (по подразбиране в `.env.example`), при празна БД се създават демо акаунти: **`nikol` / `demo123`**. С `SEED_DEMO_DATA=0` няма автоматичен сийд — импортирай каталог и регистрирай потребители през UI.

### Docker

```bash
export SESSION_SECRET="$(openssl rand -hex 32)"
docker compose up --build
```

- **`SESSION_SECRET`** е задължителен в compose (променливата от средата на хоста се подава в контейнера).
- Има **`GET /health`** (живот) и **`GET /api/ready`** (проверка към БД).
- `docker-compose.yml` монтира **volume** `/app/data` — при първи старт той е празен и **не** съдържа `books.csv` от образа. Копирай каталога в volume и импортирай:

  ```bash
  docker compose cp data/books.csv web:/app/data/books.csv
  docker compose exec web python -m book_recsys.import_csv
  ```

  Алтернатива: в compose монтирай `./data:/app/data` и пак `python -m book_recsys.import_csv` (очаква се `data/books.csv` спрямо работната директория на приложението).

## API (сесия)

- `GET /health` — liveness (без БД).
- `GET /api/ready` — готовност (ping към БД).
- `GET /api/meta` — версия, `environment`, `book_count`, дали каталожният импорт изисква token, дали има зададен `OPENAI_API_KEY` (инфо за UI).

Повечето персонални маршрути изискват **cookie** от `/api/auth/login` или `/api/auth/register` (в браузъра това е автоматично; за `curl` ползвай `-c`/`-b`).

- `POST /api/auth/register` — тяло може да включва `survey` (`reading_pace`, `moods`, `formats`, `note`) за още сигнал преди cold start таба.
- `GET /api/auth/me` — текущ потребител или `null`
- `POST /api/auth/login`, `POST /api/auth/logout`
- `POST /api/catalog/fetch-openlibrary` — `{ "query", "limit", "fetch_descriptions"? }`; при `CATALOG_ADMIN_TOKEN` — заглавка `X-Catalog-Token`.
- `GET /api/onboarding/taste-deck?n=12` — произволни книги без досегашни взаимодействия
- `POST /api/onboarding/complete` — пълен cold start (genres, language, authors, quick_reactions)
- `GET /api/recommendations?k=12&diversity=0.72`
- `POST /api/interactions` — тяло: `{ "book_id", "event_type", "rating"?, "comment"? }`
- `GET /api/library`, `GET|POST /api/friends`, …
- `GET /api/evaluation?k=10` — без вход; връща P@K/R@K/NDCG за Popular, CBF, CF, Hybrid, Hybrid+Social, плюс **проблем / хипотеза / методология / ограничения** и брой потребители във fold (за текстов отчет и таб „Оценка“).

## Структура на кода (кратко)

```
book_recsys/
  main.py           # FastAPI + сесии
  auth_pass.py      # bcrypt
  data_io.py        # CSV, upsert, Open Library URL
  open_library.py  # търсене + импорт batch
  fetch_catalog.py # CLI импорт от OL
  ...
data/books.csv
```

## Как можем да оценим проекта (рубрика)

| Критерий | Какво гледаме | Тегло (примерно) |
|----------|----------------|------------------|
| **Функционалност** | Регистрация/вход, cold start по заданието, feed, каталог с корици, приятели, feedback. | 25% |
| **Алгоритми** | CBF + CF + social + hybrid; cold‑start тежести; **MMR**. | 25% |
| **Оценяване** | Leave‑last‑out, P@K, R@K, NDCG@K. | 20% |
| **Код** | Модули, типове, ясни слоеве. | 15% |
| **UX / демо** | Работещо демо, ясен интерфейс. | 15% |

## Бележки

- Метриките на малък синтетичен каталог са за **сравнение на методи**.  
- Пълен ресийд: `from book_recsys.seed import reset_and_seed; reset_and_seed()`.

## Автори (от презентацията)

Никол Николаева, Габриела Костева — ФМИ, курс по препоръчващи системи.
