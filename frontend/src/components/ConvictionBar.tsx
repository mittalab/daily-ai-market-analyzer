interface Props { score: number; }

function barColor(score: number): string {
  if (score >= 75) return 'bg-green-500';
  if (score >= 55) return 'bg-amber-500';
  if (score >= 35) return 'bg-blue-500';
  return 'bg-gray-400';
}

export default function ConvictionBar({ score }: Props) {
  const pct = Math.min(100, Math.max(0, score));
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor(score)}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-sm font-semibold text-gray-700 w-14 text-right">{score}/100</span>
    </div>
  );
}
