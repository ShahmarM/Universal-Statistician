# Universal Statistician — веб-дашборд

React + Vite + TypeScript, три вкладки (Поиск / Ряд / Сравнение) поверх REST
API из `../src/universal_statistician/api.py`. Подробности — в
[корневом README](../README.md#веб-дашборд) и [plan.md](../plan.md).

```bash
npm install
npm run dev      # http://127.0.0.1:5173, ждёт API на http://127.0.0.1:8000
npm run build    # прод-сборка в dist/
```

По умолчанию API-клиент (`src/api.ts`) обращается к `http://127.0.0.1:8000`;
переопределить — переменной окружения `VITE_API_BASE_URL`.
