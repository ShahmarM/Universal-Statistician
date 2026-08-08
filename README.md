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
- Источник в реестре: **World Bank — World Development Indicators** (`WB_WDI`).

## Покрытие источников

| Источник | Провайдер | Статус |
|---|---|---|
| World Bank (WDI) | `SDMXProvider` | ✅ |
| Eurostat, IMF, OECD | `SDMXProvider` | план (следующий этап) |
| Росстат / ЕМИСС | новый non-SDMX provider | вне MVP |

## Установка

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Тесты

```bash
pytest                # офлайн-тесты (по умолчанию сеть не используется)
pytest -m network      # + живой запрос к World Bank SDMX API (нужен доступ в интернет)
```

Провайдерная логика тестируется на реальных объектах `sdmx.model.DataSet`,
собранных в памяти (см. `tests/conftest.py`), поэтому офлайн-тесты проверяют
настоящий код разбора ответа `sdmx.to_pandas`, а не выдуманную структуру.
Живой тест (`-m network`) бьёт в `api.worldbank.org` напрямую и в некоторых
песочницах может быть заблокирован сетевой политикой — это ограничение
окружения, а не самого провайдера.

## Пример использования ядра

```python
from universal_statistician.core.engine import default_engine

engine = default_engine()
result = engine.get_series("WB_WDI", "SP_POP_TOTL", "AFG", start_period="2011", end_period="2020")
print(result.as_dict())
```

Каждый результат несёт `attribution`: источник, код датасета, момент получения
и ссылку на первоисточник — это требование "только официальные источники"
реализовано на уровне типов, а не как соглашение.
