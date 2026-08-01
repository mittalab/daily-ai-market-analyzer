import { useState } from 'react';
import BottomNav, { type Screen } from './components/BottomNav';
import ChatWidget from './components/ChatWidget';
import TodayScreen from './screens/TodayScreen';
import ActiveTradesScreen from './screens/ActiveTradesScreen';
import AnalyseScreen from './screens/AnalyseScreen';
import PerformanceScreen from './screens/PerformanceScreen';
import DeepAnalysisScreen from './screens/DeepAnalysisScreen';
import { clearAllCache, isCachePresent } from './cache';

export default function App() {
  const [screen,     setScreen]     = useState<Screen>('today');
  const [refreshKey, setRefreshKey] = useState(0);
  const [showBanner, setShowBanner] = useState(() => isCachePresent());

  function handleClearCache() {
    clearAllCache();
    setShowBanner(false);
    setRefreshKey(k => k + 1);
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="w-full md:max-w-4xl lg:max-w-6xl mx-auto flex flex-col relative">
        {showBanner && (
          <div className="flex items-center justify-between px-4 py-2 bg-amber-50 border-b border-amber-200 text-xs">
            <span className="text-amber-700 flex items-center gap-1.5">
              <svg className="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8l1 12a2 2 0 002 2h8a2 2 0 002-2l1-12M10 12v4m4-4v4" />
              </svg>
              Showing cached data from last analysis
            </span>
            <button
              onClick={handleClearCache}
              className="ml-3 flex-shrink-0 text-xs font-semibold text-amber-900 bg-amber-100 hover:bg-amber-200 border border-amber-300 rounded-md px-2.5 py-1 transition-colors"
            >
              Load Fresh
            </button>
          </div>
        )}

        <main className="flex-1 overflow-y-auto pb-20">
          {/* All screens stay mounted — CSS hidden preserves state across tab switches */}
          <div className={screen !== 'today'   ? 'hidden' : ''}><TodayScreen  refreshKey={refreshKey} /></div>
          <div className={screen !== 'deep'    ? 'hidden' : ''}><DeepAnalysisScreen refreshKey={refreshKey} /></div>
          <div className={screen !== 'active'  ? 'hidden' : ''}><ActiveTradesScreen refreshKey={refreshKey} /></div>
          <div className={screen !== 'analyse' ? 'hidden' : ''}><AnalyseScreen active={screen === 'analyse'} /></div>
          <div className={screen !== 'status'  ? 'hidden' : ''}><PerformanceScreen /></div>
        </main>
        <BottomNav active={screen} onChange={setScreen} />
      </div>
      <ChatWidget />
    </div>
  );
}
