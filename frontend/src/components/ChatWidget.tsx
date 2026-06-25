import { useEffect, useRef, useState } from 'react';
import { checkChatContext, fetchChatContextText, sendChatMessage } from '../api';
import type { ChatMessage } from '../api';

type LoadState  = 'idle' | 'loading' | 'success' | 'error' | 'no_session';
type WidgetMode = 'intro' | 'chat';

interface ChatErr { code: string; message: string }

function formatBadgeDate(isoDate: string): string {
  try {
    const d = new Date(isoDate + 'T00:00:00');
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
  } catch {
    return isoDate;
  }
}

function hoursAgo(ts: number): number {
  return Math.floor((Date.now() - ts) / 3_600_000);
}

function countdown10pm(): string {
  const now    = new Date();
  const target = new Date();
  target.setHours(22, 0, 0, 0);
  if (now >= target) return '';
  const diff = target.getTime() - now.getTime();
  const h    = Math.floor(diff / 3_600_000);
  const m    = Math.floor((diff % 3_600_000) / 60_000);
  return `${h}h ${m}m`;
}

async function copyToClipboard(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const el = document.createElement('textarea');
    el.value          = text;
    el.style.position = 'fixed';
    el.style.opacity  = '0';
    document.body.appendChild(el);
    el.focus();
    el.select();
    document.execCommand('copy');
    document.body.removeChild(el);
  }
}

const SAMPLE_QUESTIONS = [
  'Why WATCH and not Trade Ready for {top}?',
  'I disagree with your volume assessment',
  'What would change your conviction?',
  'What if Nifty drops 300 points tomorrow?',
  'Compare tonight\'s top two setups',
];

const GREETING: ChatMessage = {
  role:    'assistant',
  content: "I'm ready to discuss tonight's analysis. What would you like to explore — a specific setup, the market context, or something else?",
};

export default function ChatWidget() {
  const [isExpanded,     setIsExpanded]     = useState(false);
  const [loadState,      setLoadState]      = useState<LoadState>('idle');
  const [widgetMode,     setWidgetMode]     = useState<WidgetMode>('intro');
  const [sessionDate,    setSessionDate]    = useState<string | null>(null);
  const [sessionId,      setSessionId]      = useState<string | null>(null);
  const [lastLoadedDate, setLastLoadedDate] = useState<string | null>(null);
  const [lastLoadedAt,   setLastLoadedAt]   = useState<number | null>(null);
  const [error,          setError]          = useState<ChatErr | null>(null);
  const [toastMsg,       setToastMsg]       = useState<string | null>(null);
  const [showReload,     setShowReload]     = useState(false);
  const [timeLeft,       setTimeLeft]       = useState('');
  const [isMobile,       setIsMobile]       = useState(window.innerWidth < 640);

  // chat state
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([GREETING]);
  const [chatInput,    setChatInput]    = useState('');
  const [isSending,    setIsSending]    = useState(false);
  const [sessionCost,  setSessionCost]  = useState(0);

  const cancelRef      = useRef(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef    = useRef<HTMLTextAreaElement>(null);

  // Initial HEAD check + poll every 5 minutes while collapsed
  useEffect(() => {
    checkChatContext().then(info => {
      if (info?.date)      setSessionDate(info.date);
      if (info?.sessionId) setSessionId(info.sessionId);
    });

    const pollId = setInterval(() => {
      if (!isExpanded) {
        checkChatContext().then(info => {
          if (info?.date) setSessionDate(info.date);
        });
      }
    }, 5 * 60 * 1000);

    const onResize = () => setIsMobile(window.innerWidth < 640);
    window.addEventListener('resize', onResize);
    return () => { clearInterval(pollId); window.removeEventListener('resize', onResize); };
  }, [isExpanded]);

  // Countdown timer
  useEffect(() => {
    if (loadState !== 'error' && loadState !== 'no_session') return;
    setTimeLeft(countdown10pm());
    const id = setInterval(() => setTimeLeft(countdown10pm()), 60_000);
    return () => clearInterval(id);
  }, [loadState]);

  // Show reload link 30 s after success
  useEffect(() => {
    if (loadState !== 'success') { setShowReload(false); return; }
    const id = setTimeout(() => setShowReload(true), 30_000);
    return () => clearTimeout(id);
  }, [loadState]);

  // Auto-clear toast
  useEffect(() => {
    if (!toastMsg) return;
    const id = setTimeout(() => setToastMsg(null), 2500);
    return () => clearTimeout(id);
  }, [toastMsg]);

  // Auto-scroll chat to bottom
  useEffect(() => {
    if (widgetMode === 'chat') {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [chatMessages, widgetMode, isSending]);

  // ── Intro flow ────────────────────────────────────────────────────────────

  async function handleLoad() {
    cancelRef.current = false;
    setLoadState('loading');
    try {
      const text = await fetchChatContextText();
      if (cancelRef.current) return;
      await copyToClipboard(text);
      setLastLoadedDate(sessionDate);
      setLastLoadedAt(Date.now());
      setLoadState('success');
      window.open('https://claude.ai', '_blank');
    } catch (err: unknown) {
      if (cancelRef.current) return;
      const e = err as { code?: string; message?: string };
      setError({ code: e.code ?? 'unknown', message: e.message ?? 'Unknown error' });
      setLoadState(e.code === 'no_session' ? 'no_session' : 'error');
    }
  }

  function handleCancel() { cancelRef.current = true; setLoadState('idle'); }
  function handleReload()  { setLoadState('idle'); setShowReload(false); }

  async function handleCopyQuestion(q: string) {
    const filled = q.replace('{top}', 'the top stock');
    await copyToClipboard(filled);
    setToastMsg('Question copied — paste after context');
  }

  // ── Chat flow ─────────────────────────────────────────────────────────────

  function enterChatMode() {
    setChatMessages([GREETING]);
    setSessionCost(0);
    setWidgetMode('chat');
  }

  async function handleSendMessage(text?: string) {
    const content = (text ?? chatInput).trim();
    if (!content || isSending) return;

    const userMsg: ChatMessage = { role: 'user', content };
    const nextMessages         = [...chatMessages, userMsg];
    setChatMessages(nextMessages);
    setChatInput('');
    setIsSending(true);

    // Reset textarea height
    if (textareaRef.current) textareaRef.current.style.height = 'auto';

    try {
      // Send the full history (excluding the pre-filled greeting if it's the first real turn)
      const historyToSend = nextMessages.filter(m => m.role !== 'assistant' || m.content !== GREETING.content);
      const reply = await sendChatMessage(
        historyToSend.length > 0 ? historyToSend : nextMessages,
        sessionId ?? undefined,
      );
      setChatMessages(prev => [...prev, { role: 'assistant', content: reply.reply }]);
      setSessionCost(prev => prev + reply.cost_usd);
    } catch (err: unknown) {
      const e = err as { message?: string };
      setChatMessages(prev => [
        ...prev,
        { role: 'assistant', content: `⚠️ Error: ${e.message ?? 'Could not get a response. Please try again.'}` },
      ]);
    } finally {
      setIsSending(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  }

  function handleTextareaChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setChatInput(e.target.value);
    // Auto-expand textarea up to 3 lines
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 80) + 'px';
  }

  // ── Badge / staleness ─────────────────────────────────────────────────────

  const alreadyLoadedToday = lastLoadedDate && lastLoadedDate === sessionDate;
  const newSessionReady    = sessionDate && !alreadyLoadedToday;
  const isNewSession       = lastLoadedDate && sessionDate && lastLoadedDate !== sessionDate;
  const hoursElapsed       = lastLoadedAt ? hoursAgo(lastLoadedAt) : null;

  let stalenessEl: React.ReactNode = null;
  if (isExpanded && loadState === 'success' && widgetMode === 'intro') {
    if (isNewSession) {
      stalenessEl = (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mb-3 text-sm">
          <div className="font-semibold text-yellow-800">⚠️ New Analysis Available</div>
          <div className="text-yellow-700 mt-1">
            You loaded {formatBadgeDate(lastLoadedDate!)} context earlier.
            Tonight's analysis ({formatBadgeDate(sessionDate!)}) is now ready.
          </div>
          <button onClick={handleLoad} className="mt-2 text-blue-600 font-medium text-sm hover:underline">
            Load Tonight's Analysis
          </button>
        </div>
      );
    } else if (hoursElapsed !== null && hoursElapsed >= 6) {
      stalenessEl = (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-3 text-sm">
          <span className="text-amber-700">⚠️ Loaded {hoursElapsed} hours ago. </span>
          <button onClick={handleReload} className="text-blue-600 hover:underline">Reload</button>
        </div>
      );
    } else {
      stalenessEl = <div className="text-green-600 text-xs mb-3">✅ Context current</div>;
    }
  }

  // ── Chat button (shared between IDLE + SUCCESS states) ────────────────────

  const chatButton = (
    <div>
      <button
        onClick={enterChatMode}
        disabled={!sessionDate}
        className="w-full flex items-center justify-center gap-2 bg-[#1E3A5F] hover:bg-[#162d4a] disabled:opacity-40 text-white font-semibold rounded-xl py-3 text-sm transition-colors"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        Chat Here Instead
      </button>
      <p className="text-xs text-gray-400 text-center mt-1">
        Ask questions without leaving the dashboard · Claude Sonnet
      </p>
    </div>
  );

  // ── renderBody ────────────────────────────────────────────────────────────

  function renderBody() {
    switch (loadState) {
      case 'idle':
        return (
          <div className="space-y-4">
            {stalenessEl}
            <div className="text-center pt-2">
              <div className="text-4xl mb-2">📊</div>
              <div className="font-semibold text-gray-900">Tonight's Analysis Ready</div>
              {sessionDate && <div className="text-sm text-gray-500 mt-1">{formatBadgeDate(sessionDate)}</div>}
            </div>

            <button
              onClick={handleLoad}
              className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl py-3 px-4 text-sm transition-colors"
            >
              Load Context &amp; Open Claude.ai
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </button>
            <p className="text-xs text-gray-500 text-center leading-relaxed">
              Copies full context to clipboard and opens a fresh Claude.ai tab.
            </p>

            <div className="relative flex items-center gap-2">
              <hr className="flex-1 border-gray-100" />
              <span className="text-xs text-gray-300">or</span>
              <hr className="flex-1 border-gray-100" />
            </div>

            {chatButton}

            <hr className="border-gray-100" />

            <div>
              <div className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">Try asking:</div>
              <div className="space-y-1">
                {SAMPLE_QUESTIONS.map(q => (
                  <button key={q} onClick={() => handleCopyQuestion(q)}
                    className="w-full text-left text-sm text-gray-600 hover:text-blue-600 hover:bg-blue-50 rounded-lg px-3 py-2 transition-colors">
                    • {q}
                  </button>
                ))}
              </div>
            </div>
          </div>
        );

      case 'loading':
        return (
          <div className="flex flex-col items-center justify-center h-full gap-4 py-12">
            <div className="w-10 h-10 border-4 border-blue-200 border-t-blue-600 rounded-full animate-spin" />
            <div className="text-center">
              <div className="font-medium text-gray-800">Preparing analysis context...</div>
              <div className="text-sm text-gray-500 mt-1">Fetching all setups from tonight</div>
            </div>
            <button onClick={handleCancel} className="text-sm text-gray-400 hover:text-gray-600">Cancel</button>
          </div>
        );

      case 'success':
        return (
          <div className="space-y-4">
            {stalenessEl}

            <div className="bg-green-50 rounded-xl p-4">
              <div className="text-center mb-3">
                <div className="text-3xl mb-1">✅</div>
                <div className="font-semibold text-green-800">Context Loaded</div>
                <div className="text-sm text-green-600 mt-1">Copied to clipboard · Claude.ai opening</div>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-2 text-green-700"><span>✅</span><span>Step 1: Context copied to clipboard</span></div>
                <div className="flex items-center gap-2 text-gray-500"><span>⏳</span><span>Step 2: Paste in Claude.ai (Ctrl+V)</span></div>
                <div className="flex items-center gap-2 text-gray-500"><span>⏳</span><span>Step 3: Ask your questions freely</span></div>
              </div>
            </div>

            <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-sm">
              <div className="flex gap-2">
                <span>⚠️</span>
                <div className="text-amber-800">
                  <span className="font-semibold">Always paste in a FRESH Claude.ai conversation.</span>
                </div>
              </div>
            </div>

            <button onClick={() => window.open('https://claude.ai', '_blank')}
              className="w-full border border-blue-600 text-blue-600 font-semibold rounded-xl py-3 text-sm hover:bg-blue-50 transition-colors">
              Open Claude.ai Again
            </button>

            <div className="relative flex items-center gap-2">
              <hr className="flex-1 border-gray-100" />
              <span className="text-xs text-gray-300">or</span>
              <hr className="flex-1 border-gray-100" />
            </div>

            {chatButton}

            <hr className="border-gray-100" />

            <div>
              <div className="text-xs font-medium text-gray-400 uppercase tracking-wide mb-2">Try asking:</div>
              <div className="space-y-1">
                {SAMPLE_QUESTIONS.map(q => (
                  <button key={q} onClick={() => handleCopyQuestion(q)}
                    className="w-full text-left text-sm text-gray-600 hover:text-blue-600 hover:bg-blue-50 rounded-lg px-3 py-2 transition-colors">
                    • {q}
                  </button>
                ))}
              </div>
            </div>

            {showReload && (
              <div className="text-center text-sm text-gray-400 pt-1">
                Need to reload?{' '}
                <button onClick={handleReload} className="text-blue-600 hover:underline">Load fresh context →</button>
              </div>
            )}
          </div>
        );

      case 'no_session':
        return (
          <div className="flex flex-col items-center justify-center h-full gap-3 py-12 text-center">
            <div className="text-5xl">🌙</div>
            <div className="font-semibold text-gray-800">Analysis Not Available Yet</div>
            {timeLeft ? (
              <>
                <div className="text-sm text-gray-500">Tonight's pipeline runs at 10:00 PM IST</div>
                <div className="text-base font-medium text-gray-700">Starts in {timeLeft}</div>
              </>
            ) : (
              <div className="text-sm text-gray-500">Pipeline has not run yet tonight.</div>
            )}
          </div>
        );

      case 'error':
        return (
          <div className="flex flex-col items-center justify-center h-full gap-3 py-12 text-center">
            <div className="text-5xl">❌</div>
            <div className="font-semibold text-gray-800">Could Not Load Context</div>
            <div className="text-sm text-gray-500 max-w-xs">
              {error?.code === 'no_session' && timeLeft
                ? `No analysis found. Pipeline runs at 10 PM (in ${timeLeft}).`
                : error?.message?.includes('Failed to fetch') || error?.message?.includes('NetworkError')
                ? 'Connection error. Check your internet.'
                : 'Something went wrong. Try again.'}
            </div>
            <button onClick={() => { setLoadState('idle'); setError(null); }}
              className="mt-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg px-5 py-2 transition-colors">
              {error?.code === 'no_session' ? 'OK' : 'Retry'}
            </button>
          </div>
        );
    }
  }

  // ── renderChat ────────────────────────────────────────────────────────────

  function renderChat() {
    return (
      <div className="flex flex-col h-full">
        {/* Chat sub-header */}
        <div className="flex-none flex items-center justify-between px-1 pb-3 border-b border-gray-100">
          <button
            onClick={() => setWidgetMode('intro')}
            className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            Back
          </button>
          <div className="text-xs text-gray-400">
            {sessionCost > 0 ? `Cost: $${sessionCost.toFixed(4)}` : 'Claude Sonnet'}
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto py-3 space-y-3">
          {chatMessages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-relaxed ${
                  msg.role === 'user'
                    ? 'bg-[#1E3A5F] text-white rounded-br-sm'
                    : 'bg-gray-100 text-gray-900 rounded-bl-sm'
                }`}
                style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
              >
                {msg.content}
              </div>
            </div>
          ))}

          {/* Typing indicator */}
          {isSending && (
            <div className="flex justify-start">
              <div className="bg-gray-100 rounded-2xl rounded-bl-sm px-4 py-3">
                <div className="flex gap-1 items-center">
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Quick-send question chips */}
        <div className="flex-none border-t border-gray-100 pt-2 pb-1">
          <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-hide">
            {SAMPLE_QUESTIONS.slice(0, 3).map(q => {
              const filled = q.replace('{top}', 'top stock');
              return (
                <button
                  key={q}
                  onClick={() => handleSendMessage(filled)}
                  disabled={isSending}
                  className="flex-none text-xs bg-gray-50 hover:bg-gray-100 disabled:opacity-40 text-gray-600 rounded-full px-3 py-1.5 whitespace-nowrap border border-gray-200 transition-colors"
                >
                  {filled}
                </button>
              );
            })}
          </div>
        </div>

        {/* Input row */}
        <div className="flex-none flex items-end gap-2 pt-2 border-t border-gray-100">
          <textarea
            ref={textareaRef}
            value={chatInput}
            onChange={handleTextareaChange}
            onKeyDown={handleKeyDown}
            placeholder="Ask about the analysis…"
            rows={1}
            className="flex-1 resize-none rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent placeholder-gray-400"
            style={{ minHeight: '38px', maxHeight: '80px' }}
          />
          <button
            onClick={() => handleSendMessage()}
            disabled={!chatInput.trim() || isSending}
            className="flex-none w-9 h-9 rounded-xl bg-[#1E3A5F] hover:bg-[#162d4a] disabled:opacity-40 flex items-center justify-center transition-colors"
          >
            <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
          </button>
        </div>
      </div>
    );
  }

  // ── Layout ────────────────────────────────────────────────────────────────

  const panelClass = isMobile
    ? 'fixed inset-0 z-[1000] flex flex-col bg-white'
    : 'fixed bottom-0 right-0 w-[360px] h-[540px] z-[1000] flex flex-col bg-white shadow-2xl rounded-tl-2xl overflow-hidden';

  const panelStyle: React.CSSProperties = {
    transform:  isExpanded ? 'translateY(0)' : 'translateY(110%)',
    transition: 'transform 300ms ease-out',
  };

  const headerSubline = widgetMode === 'chat' && sessionCost > 0
    ? `Session: ${formatBadgeDate(sessionDate ?? '')} · Chat · $${sessionCost.toFixed(4)}`
    : sessionDate
    ? `Session: ${formatBadgeDate(sessionDate)}`
    : undefined;

  return (
    <>
      {/* Floating bubble */}
      {!isExpanded && (
        <button
          onClick={() => setIsExpanded(true)}
          className="fixed bottom-[80px] right-4 z-[1000] w-14 h-14 rounded-full shadow-lg flex items-center justify-center"
          style={{ backgroundColor: '#1E3A5F' }}
          aria-label="Open analysis chat"
        >
          <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          {newSessionReady && (
            <span className="absolute -top-1 -right-1 flex items-center justify-center bg-red-500 text-white text-[10px] font-bold rounded-full min-w-[20px] h-5 px-1 animate-pulse">
              {formatBadgeDate(sessionDate!)}
            </span>
          )}
          {alreadyLoadedToday && !newSessionReady && (
            <span className="absolute -top-1 -right-1 w-3 h-3 bg-green-400 rounded-full border-2 border-white" />
          )}
        </button>
      )}

      {/* Expanded panel */}
      <div className={panelClass} style={isExpanded ? {} : panelStyle}>
        {/* Header */}
        <div className="flex-none" style={{ backgroundColor: '#1E3A5F' }}>
          <div className="flex items-center justify-between px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="text-lg">🤖</span>
              <span className="text-white font-semibold text-sm">
                {widgetMode === 'chat' ? 'Chat with Analysis' : "Discuss Tonight's Analysis"}
              </span>
            </div>
            <button
              onClick={() => setIsExpanded(false)}
              className="text-white/80 hover:text-white p-1 rounded"
              aria-label="Close"
            >
              {isMobile ? (
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              )}
            </button>
          </div>
          {headerSubline && (
            <div className="px-4 pb-2 text-white/60 text-xs">{headerSubline}</div>
          )}
        </div>

        {/* Body */}
        <div className={`flex-1 overflow-y-auto ${widgetMode === 'chat' ? 'p-3' : 'p-4'} relative`}>
          {widgetMode === 'chat' ? renderChat() : renderBody()}
        </div>

        {/* Toast */}
        {toastMsg && (
          <div className="absolute bottom-20 left-1/2 -translate-x-1/2 bg-gray-800 text-white text-xs font-medium rounded-full px-4 py-2 shadow-lg pointer-events-none z-[1010]">
            {toastMsg}
          </div>
        )}
      </div>

      {/* Desktop collapse button when expanded */}
      {isExpanded && !isMobile && (
        <button
          onClick={() => setIsExpanded(false)}
          className="fixed bottom-[80px] right-4 z-[999] w-14 h-14 rounded-full shadow-lg flex items-center justify-center opacity-30 hover:opacity-60 transition-opacity"
          style={{ backgroundColor: '#1E3A5F' }}
          aria-label="Collapse"
        >
          <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      )}
    </>
  );
}
