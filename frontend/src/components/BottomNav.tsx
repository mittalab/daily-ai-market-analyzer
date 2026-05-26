export type Screen = 'today' | 'deep' | 'watchlist' | 'analyse' | 'status';

interface Tab { id: Screen; label: string; icon: string; }

const TABS: Tab[] = [
  { id: 'today',       label: 'Today',   icon: '🏠' },
  { id: 'deep',        label: 'Deep',    icon: '🧠' },
  { id: 'watchlist',   label: 'Watch',   icon: '👁' },
  { id: 'analyse',     label: 'Analyse', icon: '🔍' },
  { id: 'status',      label: 'Status',  icon: '⚙️' },
];

interface Props { active: Screen; onChange: (s: Screen) => void; }

export default function BottomNav({ active, onChange }: Props) {
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 bg-white border-t border-gray-200">
      <div className="max-w-lg mx-auto flex">
        {TABS.map(tab => {
          const isActive = tab.id === active;
          return (
            <button
              key={tab.id}
              onClick={() => onChange(tab.id)}
              className={`flex-1 flex flex-col items-center py-2 pt-1 transition-colors duration-150 relative ${
                isActive ? 'text-blue-600' : 'text-gray-400'
              }`}
            >
              {isActive && (
                <span className="absolute top-0 left-1/2 -translate-x-1/2 w-8 h-0.5 bg-blue-600 rounded-b" />
              )}
              <span className="text-xl leading-tight">{tab.icon}</span>
              <span className="text-xs mt-0.5 font-medium">{tab.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
