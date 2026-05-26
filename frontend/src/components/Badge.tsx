import type { Stage } from '../types';

interface Props { stage: Stage; }

const config: Record<Stage, string> = {
  TRADE_READY: 'bg-green-100 text-green-800 border border-green-200',
  WATCH:       'bg-amber-100 text-amber-800 border border-amber-200',
  ON_RADAR:    'bg-blue-100 text-blue-700 border border-blue-200',
  SKIP:        'bg-gray-100 text-gray-600 border border-gray-200',
};

const labels: Record<Stage, string> = {
  TRADE_READY: 'Trade Ready',
  WATCH:       'Watch',
  ON_RADAR:    'On Radar',
  SKIP:        'Skip',
};

export default function Badge({ stage }: Props) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${config[stage]}`}>
      {labels[stage]}
    </span>
  );
}
