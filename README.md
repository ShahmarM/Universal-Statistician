# Universal Statistician

Универсальный статистический помощник: нормализованный, атрибутированный доступ
к официальной статистике из международных источников. Полный план (анализ
референсов, стек, границы MVP, порядок разработки) — в `plan.md` этой ветки/PR.

## Статус

MVP в разработке. Реализовано:

- `Provider`-интерфейс (`src/universal_statistician/providers/base.py`) — контракт,
  который должен реализовать любой источник данных.
- `SDMXProvider` (`src/universal_statistician/providers/sdmx_provider.py`) — один
  провайдер для любого источника, говорящего на SDMX (обёртка над
  [`sdmx1`](https://github.com/khaeru/sdmx)). Новый источник добавляется записью в
  `providers/registry.py`, без изменения кода провайдера.
- `QueryEngine` (`src/universal_statistician/core/engine.py`) — точка входа для всех
  будущих интерфейсов (MCP-сервер, CLI, API).

Важный нюанс, вскрывшийся при добавлении второго и третьего источника: запись в
реестре — это не "агентство целиком", а **конкретный запрашиваемый датасет**
(источник + датафлоу + зафиксированные измерения). У World Bank один датафлоу
(WDI) с одинаковым порядком измерений для всех ~1500 индикаторов — одной записи
достаточно на весь источник. У Eurostat и IMF на каждый датафлоу — свой DSD со
своим набором и порядком измерений, поэтому запись в реестре относится к одному
датафлоу (например, `ESTAT_NAMA_10_GDP`), а не ко всему агентству. Подробности и
источники правды по ключам — в `providers/registry.py`.

## Покрытие источников

| Источник | Датафлоу | Провайдер | Статус |
|---|---|---|---|
| World Bank | WDI (все индикаторы) | `SDMXProvider` | ✅ `WB_WDI` |
| IMF | CPI (Consumer Price Index) | `SDMXProvider` | ✅ `IMF_DATA_CPI` |
| Eurostat | NAMA_10_GDP (нацсчета/ВВП, текущие цены) | `SDMXProvider` | ✅ `ESTAT_NAMA_10_GDP` |
| OECD | — | `SDMXProvider` | не добавлен: нет проверенного рабочего примера запроса (см. `registry.py`) |
| Росстат / ЕМИСС | — | новый non-SDMX provider | вне MVP |

## Установка

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Тесты

```bash
pytest                 # офлайн-тесты (по умолчанию сеть не используется)
pytest -m network      # + живые запросы к World Bank / IMF / Eurostat SDMX API (нужен доступ в интернет)
```

Провайдерная логика тестируется на реальных объектах `sdmx.model.DataSet`,
собранных в памяти (см. `tests/conftest.py`), поэтому офлайн-тесты проверяют
настоящий код разбора ответа `sdmx.to_pandas`, а не выдуманную структуру.
Значения ключей (`_build_key`) сверяются с примерами из собственного
интеграционного тест-сьюта `sdmx1` (`sdmx/tests/test_sources.py`) — то есть с
запросами, которые мейнтейнеры библиотеки гоняют против живых API. Живые
тесты (`-m network`) бьют в `api.worldbank.org`, `api.imf.org`/`data.imf.org`,
`ec.europa.eu` напрямую и в этой песочнице заблокированы сетевой политикой
(egress 403) — это ограничение окружения, а не самих провайдеров.

## Пример использования ядра

```python
from universal_statistician.core.engine import default_engine

engine = default_engine()
result = engine.get_series("WB_WDI", "SP_POP_TOTL", "AFG", start_period="2011", end_period="2020")
print(result.as_dict())

# Eurostat: GDP (na_item="B1GQ") for Luxembourg ("geo"="LU")
gdp = engine.get_series("ESTAT_NAMA_10_GDP", "B1GQ", "LU", start_period="2012", end_period="2015")

# IMF: CPI food category (COICOP "CP01") for ref_area "111"
cpi = engine.get_series("IMF_DATA_CPI", "CP01", "111", start_period="2018")
```

Каждый результат несёт `attribution`: источник, код датасета, момент получения
и ссылку на первоисточник — это требование "только официальные источники"
реализовано на уровне типов, а не как соглашение.
