import { useState } from 'react';
import './App.css';
import AskTab from './components/AskTab';
import SearchTab from './components/SearchTab';
import SeriesTab from './components/SeriesTab';
import CompareTab from './components/CompareTab';

type Tab = 'ask' | 'search' | 'series' | 'compare';

const TABS: { id: Tab; label: string }[] = [
  { id: 'ask', label: 'Спросить' },
  { id: 'search', label: 'Поиск' },
  { id: 'series', label: 'Ряд' },
  { id: 'compare', label: 'Сравнение' },
];

export default function App() {
  const [tab, setTab] = useState<Tab>('ask');

  return (
    <div className="app">
      <header>
        <h1>Universal Statistician</h1>
        <p className="subtitle">
          Официальная статистика, нормализованная и атрибутированная к источнику.
        </p>
      </header>

      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? 'active' : ''}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main>
        {tab === 'ask' && <AskTab />}
        {tab === 'search' && <SearchTab />}
        {tab === 'series' && <SeriesTab />}
        {tab === 'compare' && <CompareTab />}
      </main>
    </div>
  );
}
