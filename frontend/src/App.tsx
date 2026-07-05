import { useState } from 'react';
import BottomNav, { type Screen } from './components/BottomNav';
import ChatWidget from './components/ChatWidget';
import TodayScreen from './screens/TodayScreen';
import ActiveTradesScreen from './screens/ActiveTradesScreen';
import AnalyseScreen from './screens/AnalyseScreen';
import PerformanceScreen from './screens/PerformanceScreen';
import DeepAnalysisScreen from './screens/DeepAnalysisScreen';

export default function App() {
  const [screen, setScreen] = useState<Screen>('today');

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-lg mx-auto flex flex-col relative">
        <main className="flex-1 overflow-y-auto pb-20">
          {/* All screens stay mounted — CSS hidden preserves state across tab switches */}
          <div className={screen !== 'today'   ? 'hidden' : ''}><TodayScreen /></div>
          <div className={screen !== 'deep'    ? 'hidden' : ''}><DeepAnalysisScreen /></div>
          <div className={screen !== 'active'  ? 'hidden' : ''}><ActiveTradesScreen /></div>
          <div className={screen !== 'analyse' ? 'hidden' : ''}><AnalyseScreen /></div>
          <div className={screen !== 'status'  ? 'hidden' : ''}><PerformanceScreen /></div>
        </main>
        <BottomNav active={screen} onChange={setScreen} />
      </div>
      <ChatWidget />
    </div>
  );
}
