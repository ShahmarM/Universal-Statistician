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
mcp_server.py / cli.py / api.py   тонкие обёртки трёх интерфейсов поверх tools.py
tools.py                 search_indicator, get_series, compare, list_sources, describe_source
core/compose.py          сравнительные таблицы поверх нескольких get_series() + вычисляемые колонки
core/engine.py           QueryEngine — единая точка входа: провайдеры + Catalog + Cache
core/catalog.py          локальный многоязычный полнотекстовый индекс индикаторов (SQLite FTS5)
core/cache.py            локальный TTL-кэш поверх get_series() (SQLite)
providers/sdmx_provider.py + registry.py   генерик-провайдер поверх sdmx1, источники — записи в реестре
```

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

## Покрытие источников

| Источник | Датафлоу | Статус |
|---|---|---|
| World Bank | WDI (все индикаторы) | ✅ `WB_WDI` |
| IMF | CPI (Consumer Price Index) | ✅ `IMF_DATA_CPI` |
| Eurostat | NAMA_10_GDP (нацсчета/ВВП, текущие цены) | ✅ `ESTAT_NAMA_10_GDP` |
| OECD | — | не добавлен: нет проверенного рабочего примера запроса (см. `registry.py`) |
| Росстат / ЕМИСС | — | вне MVP (нужен non-SDMX provider) |

Каталог индикаторов пока содержит только три уже проверенных в `get_series()`
записи — по одной на источник (`providers/catalog_seed.py`). Полное покрытие
каждого датафлоу требует живого запроса к codelist/conceptscheme источника,
недоступного в песочнице разработки (см. «Тесты» ниже).

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
pytest -m network      # + живые запросы к World Bank / IMF / Eurostat SDMX API (нужен доступ в интернет)
```

Провайдерная логика тестируется на реальных объектах `sdmx.model.DataSet`,
собранных в памяти (`tests/conftest.py`), а не на выдуманной структуре.
Значения ключей (`_build_key`) сверяются с примерами из собственного
интеграционного тест-сьюта `sdmx1` (`sdmx/tests/test_sources.py`) — с
запросами, которые мейнтейнеры библиотеки гоняют против живых API. Живые
тесты (`-m network`) бьют в `api.worldbank.org`, `data.imf.org`,
`ec.europa.eu` напрямую; в песочнице разработки они заблокированы сетевой
политикой (egress 403 через прокси) — это ограничение окружения, а не самих
провайдеров, и такие тесты стоит прогнать перед реальным использованием
на машине с доступом в интернет.

Все три интерфейса тестируются через свои собственные протоколы вызова
(`server.call_tool(...)`, `typer.testing.CliRunner`, `fastapi.testclient.TestClient`),
а не только через `tools.py` напрямую — именно так нашёлся баг с потоками,
описанный выше.

## Что дальше

Границы MVP, обоснование решений и полная последовательность разработки — в
[`plan.md`](./plan.md). MVP и REST API готовы. Дальше: веб-дашборд поверх
FastAPI → национальные источники (Росстат/ЕМИСС) → отдельное чат-приложение.
