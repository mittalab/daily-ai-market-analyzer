import { useState } from 'react';

interface Props {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}

export default function Expander({ title, children, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="border-t border-gray-100">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between py-3 text-left transition-colors duration-150 hover:bg-gray-50"
      >
        <span className="text-sm font-semibold text-gray-800">{title}</span>
        <svg
          className={`w-4 h-4 text-gray-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <div className="pb-4 text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">
          {children}
        </div>
      )}
    </div>
  );
}
