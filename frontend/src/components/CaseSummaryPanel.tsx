import React, { useState, useEffect, useRef } from 'react';
import { api } from '../api';
import type { CaseSummary, SummarySection, ChatAnswer } from '../api';
import {
  FileText,
  Sparkles,
  Bot,
  Send,
  CheckCircle2,
  AlertTriangle,
  ShieldCheck,
  RotateCw,
  UserCheck,
  ChevronRight,
  ExternalLink,
  Info,
  Clock,
  History,
  AlertCircle,
  Hash,
  Search,
} from 'lucide-react';

interface CaseSummaryPanelProps {
  caseId: string;
  datasetId?: string;
  officerName?: string;
  onCitationClick?: (recordId: string) => void;
  onAccountClick?: (accountId: string) => void;
}

interface ChatMessage {
  id: string;
  sender: 'user' | 'assistant';
  text: string;
  citations: string[];
  source?: string;
  cached?: boolean;
  timestamp: string;
}

export const CaseSummaryPanel: React.FC<CaseSummaryPanelProps> = ({
  caseId,
  datasetId,
  officerName = 'Investigating Officer',
  onCitationClick,
  onAccountClick,
}) => {
  // Case & Account State
  const [activeCaseId, setActiveCaseId] = useState<string>(caseId || '');
  const [inputAccountId, setInputAccountId] = useState<string>('');
  const [openingCase, setOpeningCase] = useState<boolean>(false);
  const [accountError, setAccountError] = useState<string | null>(null);
  const [caseNotice, setCaseNotice] = useState<string | null>(null);
  const [recentAccounts, setRecentAccounts] = useState<string[]>(() => {
    try {
      const stored = sessionStorage.getItem('anvitrace_session_accounts');
      return stored ? JSON.parse(stored) : [];
    } catch {
      return [];
    }
  });

  // Summary State
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  const [history, setHistory] = useState<CaseSummary[]>([]);
  const [loadingSummary, setLoadingSummary] = useState<boolean>(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [approving, setApproving] = useState<boolean>(false);
  const [approvalMessage, setApprovalMessage] = useState<string | null>(null);

  // Chat State
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputQuestion, setInputQuestion] = useState<string>('');
  const [chatLoading, setChatLoading] = useState<boolean>(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [presets, setPresets] = useState<string[]>([
    'Where did the money go?',
    'Which account is highest risk?',
    'Why was this account flagged?',
    'What are the recommended next steps?',
  ]);

  // Active Subtab: 'summary' or 'chat'
  const [activeSubtab, setActiveSubtab] = useState<'summary' | 'chat'>('summary');

  // Health Status Chip State
  const [modelHealth, setModelHealth] = useState<{
    model_ready: boolean;
    model_hash_ok: boolean;
    mode: string;
  } | null>(null);

  // Synchronization & cancellation refs
  const activeCaseIdRef = useRef<string>(activeCaseId);
  activeCaseIdRef.current = activeCaseId;
  const chatAbortControllerRef = useRef<AbortController | null>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Reset all state when opening a new case or switching cases
  const resetAllCaseState = () => {
    // Abort any in-flight chat request silently
    if (chatAbortControllerRef.current) {
      chatAbortControllerRef.current.abort();
      chatAbortControllerRef.current = null;
    }
    // Reset all chat state
    setMessages([]);
    setInputQuestion('');
    setChatLoading(false);
    setChatError(null);

    // Reset summary state
    setSummary(null);
    setHistory([]);
    setLoadingSummary(false);
    setSummaryError(null);
    setApproving(false);
    setApprovalMessage(null);

    // Reset scroll position
    if (messagesContainerRef.current) {
      messagesContainerRef.current.scrollTop = 0;
    }
  };

  // Auto-scroll chat to latest message on new content
  useEffect(() => {
    if (activeSubtab === 'chat' && messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, chatLoading, activeSubtab]);

  // Sync prop changes
  useEffect(() => {
    if (caseId && caseId !== activeCaseId) {
      resetAllCaseState();
      setActiveCaseId(caseId);
    }
  }, [caseId]);

  // Load summary and history on mount or activeCaseId change
  useEffect(() => {
    if (!activeCaseId) return;

    let isMounted = true;
    const loadInitialData = async () => {
      setLoadingSummary(true);
      setSummaryError(null);
      try {
        const [latestSum, histList, presetList, healthRes] = await Promise.all([
          api.getLatestCaseSummary(activeCaseId).catch(() => null),
          api.getCaseSummaryHistory(activeCaseId).catch(() => []),
          api.getChatPresets(activeCaseId).catch(() => []),
          api.checkHealth().catch(() => null),
        ]);

        if (isMounted) {
          setSummary(latestSum);
          setHistory(histList);
          if (presetList && presetList.length > 0) {
            setPresets(presetList);
          }
          if (healthRes) {
            setModelHealth({
              model_ready: healthRes.model_ready,
              model_hash_ok: healthRes.model_hash_ok,
              mode: healthRes.mode,
            });
          }
        }
      } catch (err: any) {
        if (isMounted) setSummaryError(err.message || 'Error loading summary');
      } finally {
        if (isMounted) setLoadingSummary(false);
      }
    };

    loadInitialData();
    return () => {
      isMounted = false;
    };
  }, [activeCaseId]);

  // Open any account on demand
  const handleOpenAccount = async (targetId?: string) => {
    const raw = (targetId || inputAccountId).trim().toUpperCase();
    if (!raw) {
      setAccountError('Please enter an account ID.');
      return;
    }
    // Pattern validation taken from actual dataset format (alphanumeric 3-35 chars)
    if (!/^[A-Z0-9_-]{3,35}$/.test(raw)) {
      setAccountError('Invalid account format. Account ID must be alphanumeric (3-35 characters).');
      return;
    }

    // Immediately reset all chat and summary state when opening a case
    resetAllCaseState();

    setAccountError(null);
    setCaseNotice(null);
    setOpeningCase(true);

    try {
      const res = await api.openCase(raw, datasetId);
      setActiveCaseId(res.case_id);
      setInputAccountId(res.account_id);

      // Add to session-only recent accounts
      setRecentAccounts((prev) => {
        const updated = [res.account_id, ...prev.filter((a) => a !== res.account_id)].slice(0, 5);
        try {
          sessionStorage.setItem('anvitrace_session_accounts', JSON.stringify(updated));
        } catch {}
        return updated;
      });

      if (res.message) {
        setCaseNotice(res.message);
      }
    } catch (err: any) {
      setAccountError(err.message || 'Failed to open case for account.');
    } finally {
      setOpeningCase(false);
    }
  };

  // Generate / Regenerate Summary
  const handleGenerateSummary = async () => {
    if (!activeCaseId) return;
    setLoadingSummary(true);
    setSummaryError(null);
    setApprovalMessage(null);
    try {
      const newSummary = await api.createCaseSummary(activeCaseId, datasetId);
      setSummary(newSummary);
      const updatedHistory = await api.getCaseSummaryHistory(activeCaseId).catch(() => [newSummary]);
      setHistory(updatedHistory);
    } catch (err: any) {
      setSummaryError(err.message || 'Failed to generate summary');
    } finally {
      setLoadingSummary(false);
    }
  };

  // Approve Summary
  const handleApprove = async () => {
    if (!summary || !activeCaseId) return;
    setApproving(true);
    setSummaryError(null);
    try {
      const approved = await api.approveCaseSummary(activeCaseId, summary.summary_id, officerName);
      setSummary(approved);
      setApprovalMessage(`Version v${approved.version} officially approved by ${officerName}. Document export updated.`);
      const updatedHistory = await api.getCaseSummaryHistory(activeCaseId).catch(() => []);
      setHistory(updatedHistory);
    } catch (err: any) {
      setSummaryError(err.message || 'Approval failed');
    } finally {
      setApproving(false);
    }
  };

  // Send Chat Message with AbortController and case-id isolation
  const handleSendChat = async (questionToSend?: string) => {
    if (!activeCaseId) {
      setChatError('Please open a case or account first.');
      return;
    }
    const q = (questionToSend || inputQuestion).trim();
    if (!q || chatLoading) return;

    // Cancel any prior in-flight request
    if (chatAbortControllerRef.current) {
      chatAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    chatAbortControllerRef.current = controller;
    const requestCaseId = activeCaseId;

    const userMsg: ChatMessage = {
      id: `usr_${Date.now()}`,
      sender: 'user',
      text: q,
      citations: [],
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputQuestion('');
    setChatLoading(true);
    setChatError(null);

    try {
      const res: ChatAnswer = await api.sendChatMessage(requestCaseId, q, datasetId, controller.signal);
      // Discard response if user switched cases while request was in-flight
      if (requestCaseId !== activeCaseIdRef.current) {
        return;
      }
      const botMsg: ChatMessage = {
        id: `bot_${Date.now()}`,
        sender: 'assistant',
        text: res.answer,
        citations: res.citations || [],
        source: res.source,
        cached: res.cached || res.source.startsWith('cached-'),
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
      setMessages((prev) => [...prev, botMsg]);
    } catch (err: any) {
      // Discard without error if request was aborted or case changed
      if (controller.signal.aborted || err.name === 'AbortError' || requestCaseId !== activeCaseIdRef.current) {
        return;
      }
      setChatError(err.message || 'Chatbot encountered an error');
    } finally {
      if (requestCaseId === activeCaseIdRef.current) {
        setChatLoading(false);
        if (chatAbortControllerRef.current === controller) {
          chatAbortControllerRef.current = null;
        }
      }
    }
  };

  // Render text with clickable citations [REC123] and accounts
  const renderInteractiveText = (text: string) => {
    const parts = text.split(/(\[[A-Za-z0-9_\-]+\])/g);
    return parts.map((part, i) => {
      const match = part.match(/^\[([A-Za-z0-9_\-]+)\]$/);
      if (match) {
        const id = match[1];
        return (
          <button
            key={i}
            onClick={() => onCitationClick && onCitationClick(id)}
            className="inline-flex items-center gap-0.5 px-1.5 py-0.5 mx-0.5 text-xs font-mono font-medium rounded bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800 hover:bg-indigo-100 dark:hover:bg-indigo-900 transition-colors cursor-pointer"
            title={`Inspect evidence citation ${id}`}
          >
            <span>[{id}]</span>
            <ExternalLink className="w-2.5 h-2.5 opacity-70" />
          </button>
        );
      }
      return <span key={i}>{part}</span>;
    });
  };

  return (
    <div className="w-full bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden flex flex-col">
      {/* Header Bar */}
      <div className="p-4 border-b border-slate-200 dark:border-slate-800 flex flex-wrap items-center justify-between gap-3 bg-slate-50 dark:bg-slate-900">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-indigo-600 text-white">
            <Sparkles className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-semibold text-slate-900 dark:text-slate-100">
                AI Case Summarizer & Assistant
              </h3>
              <span className="text-xs px-2 py-0.5 rounded-full font-mono bg-slate-200 dark:bg-slate-800 text-slate-700 dark:text-slate-300">
                Case: {activeCaseId || 'Select Account'}
              </span>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Offline-first evidence synthesis with strict citation validation (Ollama localhost)
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Health Status Chip */}
          {modelHealth && (
            <div
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border ${
                modelHealth.model_ready && modelHealth.model_hash_ok && modelHealth.mode === 'model'
                  ? 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800'
                  : 'bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-800'
              }`}
              title={
                modelHealth.model_ready
                  ? 'Local Ollama model verified and ready'
                  : `Model offline or hash mismatch (Mode: ${modelHealth.mode})`
              }
            >
              <div
                className={`w-2 h-2 rounded-full ${
                  modelHealth.model_ready && modelHealth.model_hash_ok && modelHealth.mode === 'model'
                    ? 'bg-emerald-600'
                    : 'bg-amber-600'
                }`}
              />
              <span>
                {modelHealth.model_ready && modelHealth.model_hash_ok && modelHealth.mode === 'model'
                  ? 'Model: ready'
                  : 'Model: template mode'}
              </span>
            </div>
          )}

          {/* Subtab Toggle */}
          <div className="flex items-center bg-slate-200 dark:bg-slate-800 p-1 rounded-lg">
            <button
              onClick={() => setActiveSubtab('summary')}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all flex items-center gap-1.5 ${
                activeSubtab === 'summary'
                  ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white border border-slate-300 dark:border-slate-600'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white'
              }`}
            >
              <FileText className="w-3.5 h-3.5" />
              7-Section Summary
            </button>
            <button
              onClick={() => setActiveSubtab('chat')}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all flex items-center gap-1.5 ${
                activeSubtab === 'chat'
                  ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-white border border-slate-300 dark:border-slate-600'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white'
              }`}
            >
              <Bot className="w-3.5 h-3.5" />
              Explain This Case
            </button>
          </div>
        </div>
      </div>

      {/* Account ID Input & Session History Bar */}
      <div className="px-5 py-3 bg-slate-50 dark:bg-slate-900/90 border-b border-slate-200 dark:border-slate-800 flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[240px]">
            <input
              type="text"
              value={inputAccountId}
              onChange={(e) => {
                setInputAccountId(e.target.value.toUpperCase());
                if (accountError) setAccountError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleOpenAccount();
              }}
              placeholder="Enter Account ID to explain (e.g. BARB10022001, ACC_1001)..."
              className="w-full pl-9 pr-3 py-1.5 text-xs font-mono rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 placeholder:font-sans focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
            <Search className="w-4 h-4 text-slate-400 absolute left-2.5 top-2" />
          </div>

          <button
            onClick={() => handleOpenAccount()}
            disabled={openingCase}
            className="px-4 py-1.5 text-xs font-medium rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white disabled:opacity-50 transition-colors flex items-center gap-1.5 cursor-pointer"
          >
            {openingCase ? <RotateCw className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
            Open Case
          </button>
        </div>

        {/* Validation Error Message */}
        {accountError && (
          <div className="flex items-center gap-1.5 text-xs text-rose-600 dark:text-rose-400 font-medium">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            <span>{accountError}</span>
          </div>
        )}

        {/* Neutral Candidate / Notice Message */}
        {caseNotice && !accountError && (
          <div className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 px-2.5 py-1 rounded border border-amber-200 dark:border-amber-800">
            <Info className="w-3.5 h-3.5 shrink-0" />
            <span>{caseNotice}</span>
          </div>
        )}

        {/* Recent Session Accounts Chips */}
        {recentAccounts.length > 0 && (
          <div className="flex items-center gap-2 pt-1">
            <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400 flex items-center gap-1">
              <History className="w-3 h-3" />
              Recent:
            </span>
            <div className="flex flex-wrap items-center gap-1.5">
              {recentAccounts.map((acc) => (
                <button
                  key={acc}
                  onClick={() => handleOpenAccount(acc)}
                  className="px-2 py-0.5 text-[11px] font-mono rounded bg-slate-200/80 dark:bg-slate-800 hover:bg-indigo-100 dark:hover:bg-indigo-950/60 text-slate-700 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors border border-slate-300 dark:border-slate-700 cursor-pointer"
                >
                  {acc}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Main Tab Content */}
      {activeSubtab === 'summary' ? (
        <div className="p-5 flex flex-col gap-5">
          {/* Top Action & Metadata Ribbon */}
          <div className="flex flex-wrap items-center justify-between gap-4 p-3.5 rounded-lg bg-indigo-50/50 dark:bg-indigo-950/20 border border-indigo-100 dark:border-indigo-900/50">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-indigo-950 dark:text-indigo-200 flex items-center gap-1.5">
                <ShieldCheck className="w-4 h-4 text-indigo-600 dark:text-indigo-400" />
                AI-assisted draft - verify against evidence before approval
              </span>
              {summary && (
                <div className="flex items-center gap-2">
                  <span
                    className={`text-xs px-2.5 py-0.5 rounded-full font-semibold uppercase tracking-wider ${
                      summary.status === 'approved'
                        ? 'bg-emerald-100 dark:bg-emerald-950/70 text-emerald-800 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-800'
                        : 'bg-amber-100 dark:bg-amber-950/70 text-amber-800 dark:text-amber-300 border border-amber-300 dark:border-amber-800'
                    }`}
                  >
                    {summary.status === 'approved' ? `Approved (v${summary.version})` : `Draft (v${summary.version})`}
                  </span>

                  {/* Version History Selector */}
                  {history.length > 1 && (
                    <div className="flex items-center gap-1 text-xs">
                      <History className="w-3.5 h-3.5 text-slate-500" />
                      <select
                        value={summary.summary_id}
                        onChange={(e) => {
                          const chosen = history.find((h) => h.summary_id === e.target.value);
                          if (chosen) setSummary(chosen);
                        }}
                        className="bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded px-2 py-0.5 text-xs text-slate-800 dark:text-slate-200"
                      >
                        {history.map((h) => (
                          <option key={h.summary_id} value={h.summary_id}>
                            v{h.version} ({h.status})
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={handleGenerateSummary}
                disabled={loadingSummary}
                className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-medium rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white transition-colors disabled:opacity-50 cursor-pointer shadow-xs"
              >
                <RotateCw className={`w-3.5 h-3.5 ${loadingSummary ? 'animate-spin' : ''}`} />
                {summary ? 'Regenerate Draft (New Version)' : 'Generate Case Summary'}
              </button>

              {summary && summary.status === 'draft' && (
                <button
                  onClick={handleApprove}
                  disabled={approving || loadingSummary}
                  className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white transition-colors disabled:opacity-50 cursor-pointer shadow-xs"
                >
                  <UserCheck className="w-3.5 h-3.5" />
                  {approving ? 'Approving...' : 'Officer Approve'}
                </button>
              )}
            </div>
          </div>

          {/* Success / Error Banners */}
          {approvalMessage && (
            <div className="p-3 rounded-lg bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 text-xs text-emerald-800 dark:text-emerald-300 flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
              <span>{approvalMessage}</span>
            </div>
          )}

          {summaryError && (
            <div className="p-3 rounded-lg bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-800 text-xs text-rose-800 dark:text-rose-300 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-rose-600 shrink-0" />
              <span>{summaryError}</span>
            </div>
          )}

          {/* Loading State */}
          {loadingSummary && (
            <div className="py-12 flex flex-col items-center justify-center text-center gap-3">
              <div className="w-10 h-10 rounded-full border-3 border-indigo-600 border-t-transparent animate-spin" />
              <p className="text-sm font-medium text-slate-700 dark:text-slate-300">
                Generating structured 7-section case summary...
              </p>
              <p className="text-xs text-slate-500 max-w-md">
                Verifying facts against evidence graph, validating record citations, and applying forensic safety rules.
              </p>
            </div>
          )}

          {/* Empty State */}
          {!loadingSummary && !summary && (
            <div className="py-12 flex flex-col items-center justify-center text-center gap-3 border-2 border-dashed border-slate-200 dark:border-slate-800 rounded-xl">
              <FileText className="w-10 h-10 text-slate-400" />
              <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-200">
                No Summary Generated Yet
              </h4>
              <p className="text-xs text-slate-500 max-w-md">
                Click "Generate Case Summary" to analyze the money trail, review candidate layers, and produce an official 7-section judicial brief.
              </p>
              <button
                onClick={handleGenerateSummary}
                className="mt-2 px-4 py-2 text-xs font-semibold rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white transition-colors cursor-pointer shadow-sm"
              >
                Generate Initial Draft
              </button>
            </div>
          )}

          {/* 7-Section View */}
          {!loadingSummary && summary && (
            <div className="flex flex-col gap-4">
              {/* Validation Status Strip */}
              <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 rounded-md bg-slate-100 dark:bg-slate-800/60 text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-slate-700 dark:text-slate-300">Validator Status:</span>
                  {summary.validation.passed ? (
                    <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-400 font-medium">
                      <CheckCircle2 className="w-3.5 h-3.5" /> All facts & citations verified
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-400 font-medium">
                      <AlertTriangle className="w-3.5 h-3.5" /> Template fallback applied to unverified sections
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3 text-slate-500 text-[11px] font-mono">
                  <span>Runtime: {summary.model_info.name}</span>
                  <span>SHA-256: {summary.content_hash.substring(0, 12)}...</span>
                </div>
              </div>

              {/* Sections Accordion / Cards */}
              <div className="grid grid-cols-1 gap-3.5">
                {summary.sections.map((section: SummarySection, idx: number) => (
                  <div
                    key={idx}
                    className="p-4 rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900/90 shadow-2xs hover:border-slate-300 dark:hover:border-slate-700 transition-colors"
                  >
                    <div className="flex items-center justify-between gap-3 mb-2.5">
                      <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-2">
                        <span>{section.title}</span>
                      </h4>
                      <span
                        className={`text-[10px] font-semibold px-2 py-0.5 rounded tracking-wide ${
                          section.source && section.source.includes('Phrased')
                            ? 'bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800'
                            : 'bg-emerald-50 dark:bg-emerald-950/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800/50'
                        }`}
                      >
                        {section.source && section.source.includes('Phrased')
                          ? 'Phrased by local model'
                          : 'Verified facts (computed)'}
                      </span>
                    </div>

                    <p className="text-xs leading-relaxed text-slate-700 dark:text-slate-300">
                      {renderInteractiveText(section.text)}
                    </p>

                    {section.citations && section.citations.length > 0 && (
                      <div className="mt-3 pt-2.5 border-t border-slate-100 dark:border-slate-800/80 flex flex-wrap items-center gap-1.5 text-slate-500">
                        <span className="text-[11px] font-medium text-slate-600 dark:text-slate-400 mr-1">
                          Referenced Records:
                        </span>
                        {section.citations.map((c, citIdx) => (
                          <button
                            key={citIdx}
                            onClick={() => onCitationClick && onCitationClick(c)}
                            className="inline-flex items-center gap-1 text-[11px] font-mono font-medium px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-indigo-50 hover:text-indigo-600 dark:hover:bg-indigo-950 dark:hover:text-indigo-300 transition-colors cursor-pointer"
                          >
                            <Hash className="w-2.5 h-2.5 opacity-60" />
                            {c}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              {/* Approval Footer */}
              {summary.status === 'approved' && (
                <div className="p-3.5 rounded-lg bg-emerald-50/60 dark:bg-emerald-950/20 border border-emerald-200 dark:border-emerald-800 flex items-center justify-between text-xs text-emerald-900 dark:text-emerald-300">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                    <span>
                      <b>Officially Approved by:</b> {summary.approved_by || officerName} on{' '}
                      {summary.approved_at ? new Date(summary.approved_at).toLocaleString() : 'Recent'}
                    </span>
                  </div>
                  <span className="text-[11px] opacity-80">Version v{summary.version} is permanent and immutable.</span>
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        /* Explain This Case Chatbot */
        <div key={`chat-${activeCaseId}`} className="flex flex-col h-[520px]">
          {/* Chat Guidance Banner */}
          <div className="px-4 py-2 bg-amber-50 dark:bg-slate-800 border-b border-amber-200 dark:border-slate-700 flex items-center justify-between text-xs text-amber-900 dark:text-amber-200">
            <span className="flex items-center gap-1.5">
              <Info className="w-3.5 h-3.5 text-amber-600 shrink-0" />
              AI-generated explanation - verify against evidence. All facts are strictly bounded to this case.
            </span>
            <span className="text-[10px] px-2 py-0.5 rounded bg-amber-100 dark:bg-slate-700 border border-amber-300 dark:border-slate-600 font-mono text-amber-900 dark:text-amber-200">
              Offline Runtime
            </span>
          </div>

          {/* Preset Question Chips */}
          <div className="p-3 border-b border-slate-200 dark:border-slate-800 flex items-center gap-2 overflow-x-auto bg-slate-100 dark:bg-slate-850">
            <span className="text-xs font-semibold text-slate-600 dark:text-slate-400 whitespace-nowrap">Suggested:</span>
            {presets.map((preset, idx) => (
              <button
                key={idx}
                onClick={() => handleSendChat(preset)}
                disabled={chatLoading}
                className="text-xs px-2.5 py-1 rounded-full whitespace-nowrap bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 text-slate-800 dark:text-slate-200 hover:bg-slate-200 dark:hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 transition-colors cursor-pointer"
              >
                {preset}
              </button>
            ))}
          </div>

          {/* Messages Scroll Area */}
          <div ref={messagesContainerRef} className="flex-1 p-4 pt-4 overflow-y-auto flex flex-col gap-3.5 bg-slate-50 dark:bg-slate-900">
            {messages.length === 0 && (
              <div className="my-auto text-center flex flex-col items-center justify-center gap-2 p-6">
                <Bot className="w-8 h-8 text-slate-400" />
                <h4 className="text-sm font-semibold text-slate-700 dark:text-slate-300">
                  Ask a question about this case
                </h4>
                <p className="text-xs text-slate-500 max-w-sm">
                  Inquire about fund trail hops, candidate classifications, risk indices, or choose a suggested question above.
                </p>
              </div>
            )}

            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`flex flex-col max-w-[85%] ${
                  msg.sender === 'user' ? 'ml-auto items-end' : 'mr-auto items-start'
                }`}
              >
                <div
                  className={`p-3.5 rounded-2xl text-xs leading-relaxed ${
                    msg.sender === 'user'
                      ? 'bg-indigo-600 text-white rounded-br-none border border-indigo-700 dark:border-indigo-500'
                      : 'bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 border border-slate-200 dark:border-slate-700 rounded-bl-none'
                  }`}
                >
                  {msg.sender === 'user' ? msg.text : renderInteractiveText(msg.text)}
                </div>

                <div
                  className={`mt-1 flex items-center gap-2 px-1 text-[10px] text-slate-500 dark:text-slate-400 ${
                    msg.sender === 'user' ? 'justify-end' : 'justify-start'
                  }`}
                >
                  <span>{msg.timestamp}</span>
                  {msg.sender === 'assistant' && (
                    msg.cached || msg.source?.toLowerCase().includes('cached') ? (
                      <span className="px-1.5 py-0.5 rounded font-mono text-[10px] bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 font-medium border border-slate-300 dark:border-slate-700">
                        Cached
                      </span>
                    ) : msg.source?.includes('Phrased') ? (
                      <span className="px-1.5 py-0.5 rounded font-mono text-[10px] bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 font-medium border border-slate-300 dark:border-slate-700">
                        Phrased by local model
                      </span>
                    ) : (
                      <span className="px-1.5 py-0.5 rounded font-mono text-[10px] bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 font-medium border border-slate-300 dark:border-slate-700">
                        Verified facts (computed)
                      </span>
                    )
                  )}
                </div>
              </div>
            ))}

            {chatLoading && (
              <div className="mr-auto flex items-center gap-2 p-3 rounded-2xl bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-xs text-slate-600 dark:text-slate-400">
                <div className="w-3.5 h-3.5 rounded-full border-2 border-indigo-600 border-t-transparent animate-spin" />
                <span>Consulting offline case facts...</span>
              </div>
            )}

            {chatError && (
              <div className="p-2.5 rounded-lg bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 text-xs text-rose-700 dark:text-rose-300 flex items-center gap-2">
                <AlertCircle className="w-4 h-4 text-rose-600 shrink-0" />
                <span>{chatError}</span>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Chat Input Bar */}
          <div className="p-3 border-t border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 flex items-center gap-2">
            <input
              type="text"
              value={inputQuestion}
              onChange={(e) => setInputQuestion(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSendChat()}
              placeholder="Ask an investigation question (e.g. Where did the money go?)"
              className="flex-1 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-3.5 py-2 text-xs text-slate-900 dark:text-slate-100 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              disabled={chatLoading}
            />
            <button
              onClick={() => handleSendChat()}
              disabled={chatLoading || !inputQuestion.trim()}
              className="p-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white transition-colors disabled:opacity-40 cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
