# Universal Statistician

Универсальный статистический помощник: нормализованный, атрибутированный
доступ к официальной статистике из международных источников — за любой
период, с автоматически считаемыми сравнительными таблицами. Каждое число
несёт источник, код датасета, момент получения и ссылку на первоисточник.

Анализ референсных проектов, обоснование стека и полная последовательность
разработки MVP — в [`plan.md`](./plan.md). Ниже — что уже реализовано и как
этим пользоваться.

## Архитектура

Один слой поверх другого, каждый — с собственными тестами:

```
frontend/                React + Vite + TypeScript дашборд поверх REST API (три вкладки)
mcp_server.py / cli.py / api.py   тонкие обёртки трёх интерфейсов поверх tools.py
tools.py                 search_indicator, get_series, compare, list_sources, describe_source, refresh_catalog, catalog_stats
core/compose.py          сравнительные таблицы поверх нескольких get_series() + вычисляемые колонки
core/engine.py           QueryEngine — единая точка входа: провайдеры + Catalog + Cache
core/catalog.py          локальный многоязычный полнотекстовый индекс индикаторов (SQLite FTS5 + метаданные)
core/ingestion.py        discovery → нормализация → каталог (upsert, incremental refresh)
core/cache.py            локальный TTL-кэш поверх get_series() (SQLite)
providers/sdmx_provider.py + registry.py   генерик-провайдер поверх sdmx1, источники — записи в реестре
providers/pxweb_provider.py + pxweb_registry.py   генерик-провайдер поверх pxwebpy (второй, не-SDMX протокол)
chat.py                  CLI-чат поверх Claude API, инструменты и их схемы переиспользуются от mcp_server.py
```

Более крупная инициатива поверх этого MVP — расширение на десятки источников
и естественноязыковой статистик — расписана по фазам в
[`docs/architecture/nl-platform.md`](./docs/architecture/nl-platform.md).

`Provider` — единственный контракт (`get_series`, `describe`), который должен
реализовать источник данных; поиск (`search`) сознательно вынесен из него в
`Catalog`/`QueryEngine`, потому что он межисточниковый по своей природе.
`tools.py` — общая логика для всех трёх интерфейсов: MCP-сервера, CLI и
REST API. Каждый — тонкая обёртка над одними и теми же функциями, а не три
места с одной и той же логикой.

### Два вскрывшихся по ходу дела нюанса

**Запись в реестре — это не "агентство целиком", а конкретный запрашиваемый
датасет.** У World Bank один датафлоу (WDI) с одинаковым порядком измерений
для всех ~1500 индикаторов — одной записи достаточно на весь источник. У
Eurostat и IMF на каждый датафлоу свой DSD со своим набором и порядком
измерений, поэтому запись в реестре относится к одному датафлоу (например,
`ESTAT_NAMA_10_GDP`), а не ко всему агентству. Подробности и источники правды
по форматам ключей — в `providers/registry.py`.

**MCP-сервер выполняет каждый tool-вызов в отдельном worker-потоке.** Это
нашлось только при сквозном тесте через `server.call_tool(...)`, а не через
`tools.py` напрямую: `sqlite3`-соединения `Catalog`/`Cache` по умолчанию
(`check_same_thread=True`) такие вызовы отклоняют. Отдельное соединение на
поток не подходит — для `:memory:`-баз это была бы каждый раз новая пустая
база. Исправлено `check_same_thread=False` + `threading.Lock` на оба класса,
закреплено regression-тестами через `ThreadPoolExecutor`
(`test_catalog.py`, `test_cache.py`) и через реальный `server.call_tool()`
(`test_mcp_server.py`). REST API (`api.py`) подвержен тому же классу риска —
Starlette тоже выполняет синхронные хендлеры в worker-потоке — и прошёл
через `TestClient` без дополнительных правок: фикс уже был общим для
`Catalog`/`Cache`, а не специфичным для MCP.

**Необработанное исключение провайдера ломает CORS, а не просто отдаёт 500.**
Нашлось только при живой проверке дашборда в браузере (не через `TestClient` —
там это выглядело бы просто как 500): когда `get_series`/`compare` падают на
реальном сетевом сбое (например, `api.worldbank.org` заблокирован egress-
политикой), необработанное исключение не долетает до `CORSMiddleware`
нормально — браузер репортит это как "blocked by CORS policy", хотя
реальная причина не в CORS. Исправлено: `_call()` в `api.py` ловит
`Exception` целиком и превращает его в обычный `HTTPException(502, ...)` —
тогда CORS-заголовки проставляются штатно, а сообщение в дашборде честное
("Upstream data source request failed: …"), а не вводящее в заблуждение.
Закреплено `test_provider_failure_is_a_clean_502_with_cors_headers`.

**`PxApi(...)` (pxwebpy) делает сетевой запрос прямо в конструкторе.** В
отличие от `sdmx.Client()`, который ленивый (сеть — только при `.data()`),
`PxApi.__init__` сразу ходит за `/config` и числом таблиц. Если бы
`PXWebProvider` строил его в своём `__init__`, то `default_engine()` —
вызывается при старте КАЖДОГО интерфейса — пытался бы достучаться до SCB ещё
до того, как кто-то вообще запросил что-то из этого источника, и ронял бы
старт всего приложения при недоступности одного-единственного агентства.
Исправлено до того, как это стало багом: `PXWebProvider` строит `PxApi`
лениво при первом `get_series()`, не в конструкторе. Закреплено
`test_construction_does_not_touch_the_network`.

**`chat.py` переиспользует объект `server` из `mcp_server.py`, а не модуль.**
`from universal_statistician.mcp_server import server as _mcp_server` —
`_mcp_server` уже *есть* объект `MCPServer`, а не модуль с атрибутом
`.server`. Первая версия `execute_tool_call` писала
`_mcp_server.server.call_tool(...)` (лишний `.server`) — упало бы на первом
же вызове инструмента. Поймано офлайн-тестом (`test_execute_tool_call_*`),
без необходимости в живом диалоге с Claude.

### Каталог: от статичного seed к discovery-пайплайну (Фаза 1)

`IndicatorMeta`/`IndicatorEntry` расширены необязательными полями (`unit`,
`frequency`, `dataset_id`, `geographic_coverage`, `dimensions`,
`source_organization`, `official_url`, `last_updated`, `keywords`) —
обратно совместимо: старые записи с одним кодом и меткой (как в
`catalog_seed.py`) по-прежнему валидны, просто с пустыми новыми полями.
`Catalog.add()` теперь честный upsert по `(source_id, indicator_id)`, а не
только вставка — повторный запуск ingestion обновляет запись, а не плодит
дубликаты. Провайдер может (необязательно) реализовать
`MetadataDiscoverable.discover_catalog_entries()`
(`providers/base.py`) — тогда `core/ingestion.py` умеет выкачать его каталог
целиком через `ustat catalog refresh [SOURCE_ID]` вместо ручного
перечисления индикаторов. Ни один из зарегистрированных источников пока
этого не реализует (Фазы 2-6) — команда честно репортит
`"does not support metadata discovery"`, а не падает и не делает вид, что
что-то произошло. Подробности архитектуры — в
[`docs/architecture/nl-platform.md`](./docs/architecture/nl-platform.md).

## Покрытие источников

| Источник | Датафлоу/таблица | Статус |
|---|---|---|
| World Bank | WDI (все индикаторы) | ✅ `WB_WDI` |
| IMF | CPI (Consumer Price Index) | ✅ `IMF_DATA_CPI` |
| Eurostat | NAMA_10_GDP (нацсчета/ВВП, текущие цены) | ✅ `ESTAT_NAMA_10_GDP` |
| Statistics Sweden (SCB) | TAB6471 (PX-Web, не SDMX) | ✅ `SCB_TAB6471` |
| OECD | — | не добавлен: нет проверенного рабочего примера запроса (см. `registry.py`) |
| Росстат / ЕМИСС | — | не добавлен: см. ниже |

Каталог индикаторов пока содержит только четыре уже проверенных в
`get_series()` записи — по одной на источник (`providers/catalog_seed.py`).
Полное покрытие каждого датафлоу/таблицы требует живого запроса к
codelist/conceptscheme (SDMX) или table-variables (PX-Web) источника,
недоступного в песочнице разработки (см. «Тесты» ниже).

### Почему нет Росстат/ЕМИСС

`fedstat.ru` сам по себе заблокирован egress-политикой этой песочницы (не
только этот домен — проверено, что вообще любой внешний хост, кроме
pypi/npm/github/anthropic, недоступен), так что живая проверка исключена
полностью, даже получить одну HTML-страницу не вышло. Хуже того: у fedstat.ru
нет документированного публичного API — доступ к данным идёт через
скрейпинг HTML/JS с внутренней страницы индикатора (единственная найденная
референсная реализация, R-пакет `fedstatAPIr`, сама описывает это как
"эмпирически определённый 12-й `<script>`-тег" — то есть недокументированный
и по своей природе хрупкий механизм). Для SDMX-источников и SCB (см. выше)
у меня была возможность сверяться с проверенным тест-сьютом реальной
библиотеки — здесь такого источника истины нет и быть не может без живого
доступа. Ставить в реестр угаданную логику скрейпинга для страницы, которую
я ни разу не видел, — именно та ошибка, которую этот проект последовательно
избегает (тот же принцип, что и решение не добавлять OECD). Возвращаемся к
этому источнику, когда либо появится сетевой доступ, либо Росстат
опубликует документированный API.

## Установка

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Запуск

**MCP-сервер** (stdio-транспорт, добавить в конфиг MCP-хоста — Claude Desktop/Code и т.п.):

```bash
universal-statistician-mcp
```

Инструменты: `search_indicator`, `get_series`, `compare`, `list_sources`,
`describe_source` (сигнатуры и докстринги — в `mcp_server.py`, докстринг
становится описанием инструмента для LLM-хоста).

**CLI** — та же логика, для разработки/smoke-тестов без MCP-клиента, вывод JSON:

```bash
ustat sources
ustat source WB_WDI
ustat search population
ustat series WB_WDI SP_POP_TOTL AFG --start 2015 --end 2020
ustat compare WB_WDI --indicator SP_POP_TOTL --ref-area AFG --ref-area USA --growth --rank
ustat compare WB_WDI --indicator-id SP_POP_TOTL --indicator-id NY.GDP.MKTP.CD --for-area AFG
ustat catalog stats             # индикаторов в каталоге, по источнику
ustat catalog refresh           # discovery-ingestion для всех источников, что его поддерживают
ustat catalog refresh WB_WDI    # то же самое для одного источника
```

Некорректный запрос (неизвестный источник, неполный `compare`) печатает
понятное сообщение в stderr и завершает процесс кодом 1, а не сырым traceback.

**REST API** — та же логика по HTTP, с автогенерируемой OpenAPI-документацией:

```bash
uvicorn universal_statistician.api:app --reload
# http://127.0.0.1:8000/docs — интерактивная документация
```

| Метод и путь | Соответствует |
|---|---|
| `GET /sources` | `tools.list_sources` |
| `GET /sources/{source_id}` | `tools.describe_source` (404, если источник неизвестен) |
| `GET /search?q=...&limit=` | `tools.search_indicator` |
| `GET /series?source_id=&indicator_id=&ref_area=&start_period=&end_period=` | `tools.get_series` |
| `POST /compare` (JSON-тело = параметры `tools.compare`) | `tools.compare` (400 при некорректной форме запроса) |

**Веб-дашборд** (React + Vite + TypeScript, требует запущенный REST API):

```bash
cd frontend
npm install
npm run dev            # http://127.0.0.1:5173, ждёт API на http://127.0.0.1:8000
# или сборка статики: npm run build && npm run preview
```

Три вкладки — «Поиск» (работает без сети, поверх локального каталога),
«Ряд» и «Сравнение» (форма → таблица + линейный график через `recharts`,
с общей `_call`-обработкой ошибок так же, как в `api.py`: сетевой/HTTP-сбой
показывается понятным баннером, а не белым экраном). CORS настроен на
`api.py` под любой локальный origin (dev-сервер Vite или собранная статика).

**Чат** — интерактивный диалог с Claude, использующий те же инструменты, что
и MCP-сервер (схемы и выполнение переиспользуются напрямую от
`mcp_server.server`, не описаны и не продублированы заново):

```bash
export ANTHROPIC_API_KEY=...   # https://console.anthropic.com/
ustat chat                     # модель по умолчанию — claude-sonnet-5, флаг --model для другой
```

Без `ANTHROPIC_API_KEY` команда сразу и понятно завершается кодом 1, а не
падает при первом сетевом вызове. В этой песочнице ключа нет — живой диалог
не проверялся ни разу, только структура: конвертация схем инструментов
MCP → формат Claude API, выполнение вызовов через настоящий
`mcp_server.server.call_tool(...)`, и цикл `ChatSession` — на объектах
`anthropic.types.Message`/`TextBlock`/`ToolUseBlock`, собранных вручную
(реальные типы SDK, не выдуманная форма ответа), а не на живом клиенте.
Прогнать первый настоящий диалог и проверить его — на машине с ключом.

**Python API** напрямую через ядро:

```python
from universal_statistician.core.engine import default_engine
from universal_statistician.core.compose import compare_across_countries, with_growth, with_rank

engine = default_engine()

result = engine.get_series("WB_WDI", "SP_POP_TOTL", "AFG", start_period="2011", end_period="2020")
print(result.as_dict())

engine.search_indicator("produit")  # -> ESTAT_NAMA_10_GDP / B1GQ, по французской метке

table = compare_across_countries(engine, "WB_WDI", "SP_POP_TOTL", ["AFG", "USA", "LUX"], start_period="2015")
print(with_rank(with_growth(table)).as_dict())
```

## Тесты

```bash
pytest                 # офлайн-тесты (по умолчанию сеть не используется)
pytest -m network      # + живые запросы к World Bank / IMF / Eurostat / SCB (нужен доступ в интернет)
```

Провайдерная логика тестируется на реальных объектах `sdmx.model.DataSet`,
собранных в памяти (`tests/conftest.py`), а не на выдуманной структуре.
Значения ключей (`_build_key`) сверяются с примерами из собственного
интеграционного тест-сьюта `sdmx1` (`sdmx/tests/test_sources.py`) — с
запросами, которые мейнтейнеры библиотеки гоняют против живых API.
`PXWebProvider` тестируется тем же принципом: строки для парсинга получены
через реальную функцию `pxweb._internal.functions.unpack_table_data`,
скормленную сконструированному JSON-stat2 (`tests/test_pxweb_provider.py`),
а не через выдуманную форму ответа. Живые тесты (`-m network`) бьют в
`api.worldbank.org`, `data.imf.org`, `ec.europa.eu`, `statistikdatabasen.scb.se`
напрямую; в песочнице разработки они заблокированы сетевой политикой (egress
403 через прокси — и не только эти хосты, см. раздел про Росстат/ЕМИСС выше)
— это ограничение окружения, а не самих провайдеров, и такие тесты стоит
прогнать перед реальным использованием на машине с доступом в интернет.

Все три бэкенд-интерфейса тестируются через свои собственные протоколы вызова
(`server.call_tool(...)`, `typer.testing.CliRunner`, `fastapi.testclient.TestClient`),
а не только через `tools.py` напрямую — именно так нашёлся баг с потоками,
описанный выше.

Дашборд отдельным JS-тест-фреймворком не покрыт (соответствует объёму
проверки личного инструмента — сам REST-контракт, который он вызывает, уже
покрыт `test_api.py`). Проверен вручную живым браузером (Chromium,
Playwright) через все три вкладки: «Поиск» — полностью вживую (реальные
данные из локального каталога), «Ряд»/«Сравнение» — что форма отправляется
и HTTP/сетевая ошибка показывается понятным баннером, а не белым экраном.
Именно эта проверка (не `TestClient`) нашла баг с CORS/502, описанный выше.

`chat.py` тестируется на той же грани честности: `get_tool_schemas`/
`execute_tool_call` — реальными вызовами в `mcp_server.server` (те же
сетенезависимые инструменты, что уже проверены в `test_mcp_server.py`), а
цикл `ChatSession` — на фейковом Anthropic-клиенте, собранном из настоящих
`anthropic.types.Message`/`TextBlock`/`ToolUseBlock` (реальные типы SDK, не
угаданная форма). Без `ANTHROPIC_API_KEY` в этой песочнице живой диалог с
Claude не проверялся ни разу — честно зафиксировано, а не молчаливо
предположено.

## Оценка по внешнему бенчмарку

[`docs/benchmarks/uosa-bench-v1-assessment.md`](./docs/benchmarks/uosa-bench-v1-assessment.md) —
честная оценка продукта по присланному пользователем `UOSA-Bench v1.0`
(1000-балльный бенчмарк для статистических AI-ассистентов, 12 блоков, 7
gate-критериев). Дословно исполнить бенчмарк в этой среде нельзя (сеть
заблокирована, нет ключа Anthropic API, каталог — 4 индикатора вместо 20
стран × 15 тематик) — вместо выдуманных баллов документ даёт
классификацию по блокам/gate с точными ссылками на код и то, что реально
прогнано офлайн. Итог коротко: три самых опасных gate (фабрикация данных,
ложная атрибуция, скрытые оценки) проходятся **по конструкции кода**, но
целый блок G (revisions/vintage/конфликты источников), Confidence Status,
поле `unit`/`formula` в provenance и 15 из 20 операций Golden Derivation
Suite (CAGR, currency conversion, index rebasing и др.) — не реализованы.
Формальный вердикт бенчмарка — FAIL по правилу "любой gate fail = FAIL",
из-за GATE-05 (методологические разрывы, механизма обнаружения нет).
Документ заканчивается приоритизированным списком того, что дешевле всего
исправить в рамках уже существующей архитектуры.

## Что дальше

Границы MVP, обоснование решений и полная последовательность разработки — в
[`plan.md`](./plan.md). MVP, REST API, веб-дашборд, второй (не-SDMX)
источник и CLI-чат готовы. Росстат/ЕМИСС осознанно отложены (см. выше).

Начата более крупная инициатива — расширение на десятки источников +
естественноязыковой статистик поверх текущего ядра, по 13 фазам, статус —
в [`docs/architecture/nl-platform.md`](./docs/architecture/nl-platform.md).
Фаза 1 (архитектура каталога и нормализация метаданных) готова; следующая —
полное discovery-покрытие World Bank (Фаза 2). Отдельно, из оценки по
бенчмарку выше: `formula`/`input_series` в provenance derived-таблиц и явная
`status`-таксономия (Official/Derived/Composite/User-defined/Estimated)
запланированы как часть Фазы 10 (provenance/citation system).
