import React, { useState, useEffect } from 'react';
import { api } from './api';
import { Sidebar } from './components/Sidebar';
import type { ActiveTab } from './components/Sidebar';
import { Header } from './components/Header';
import { InvestigateWorkspace } from './components/InvestigateWorkspace';
import { CaseManagementView } from './components/CaseManagementView';
import { DocumentsView } from './components/DocumentsView';
import { DatasetView } from './components/DatasetView';
import { LandingPage } from './landing/components/LandingPage';
import { AuthModal, DUMMY_OFFICERS } from './components/AuthModal';
import type { OfficerProfile } from './components/AuthModal';

interface NavigationState {
  tab: ActiveTab;
  accountId: string;
}

const getInitialState = (): { isLanding: boolean; tab: ActiveTab; accountId: string } => {
  const hasOfficer = typeof window !== 'undefined' && Boolean(localStorage.getItem('anvitrace_officer'));
  if (typeof window !== 'undefined' && window.location.hash) {
    const hash = window.location.hash.replace('#', '');
    const [tab, acc] = hash.split('/');
    if (tab === 'overview' || tab === 'app') {
      return { isLanding: false, tab: 'cases', accountId: acc || '' };
    }
    if (['cases', 'investigate', 'documents', 'dataset-status'].includes(tab)) {
      return { isLanding: false, tab: tab as ActiveTab, accountId: acc || '' };
    }
    if (tab === 'landing') {
      return hasOfficer
        ? { isLanding: false, tab: 'cases', accountId: '' }
        : { isLanding: true, tab: 'cases', accountId: '' };
    }
  }
  return hasOfficer
    ? { isLanding: false, tab: 'cases', accountId: '' }
    : { isLanding: true, tab: 'cases', accountId: '' };
};

export const App: React.FC = () => {
  const initial = getInitialState();
  const [isLanding, setIsLanding] = useState<boolean>(initial.isLanding);
  const [activeTab, setActiveTab] = useState<ActiveTab>(initial.tab);
  const [selectedAccountId, setSelectedAccountId] = useState<string>(initial.accountId);
  const [historyStack, setHistoryStack] = useState<NavigationState[]>([]);
  const [backendOnline, setBackendOnline] = useState<boolean>(false);
  const [datasetId, setDatasetId] = useState<string | undefined>(undefined);
  const [selectedDocType, setSelectedDocType] = useState<'freeze_requisition' | 'case_diary'>('freeze_requisition');

  // Authentication State
  const [isAuthOpen, setIsAuthOpen] = useState<boolean>(false);
  const [officer, setOfficer] = useState<OfficerProfile | null>(() => {
    try {
      const saved = localStorage.getItem('anvitrace_officer');
      if (saved) return JSON.parse(saved);
    } catch {}
    return null;
  });

  // Responsive & Collapsible Sidebar State with localStorage persistence
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem('anvitrace_sidebar_open');
      if (saved !== null) return JSON.parse(saved);
      if (typeof window !== 'undefined') {
        return window.innerWidth >= 1024;
      }
    } catch {}
    return true;
  });

  const toggleSidebar = () => {
    setSidebarOpen((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('anvitrace_sidebar_open', JSON.stringify(next));
      } catch {}
      return next;
    });
  };

  // Keyboard shortcut: Ctrl+B or Cmd+B to toggle sidebar quickly
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
        const target = e.target as HTMLElement;
        const isInput = target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
        if (!isInput) {
          e.preventDefault();
          toggleSidebar();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Sync with browser back/forward buttons (popstate) and hash changes
  useEffect(() => {
    const checkIsAuth = () => {
      try {
        return Boolean(localStorage.getItem('anvitrace_officer'));
      } catch {
        return false;
      }
    };

    const handleUrlHash = () => {
      const isAuth = checkIsAuth();
      if (window.location.hash) {
        const hash = window.location.hash.replace('#', '');
        const [tab, acc] = hash.split('/');
        if (tab === 'landing' || !tab) {
          if (isAuth) {
            // When authenticated, NEVER fall back to landing page via URL or back button
            setIsLanding(false);
            setActiveTab('cases');
            setSelectedAccountId('');
            if (window.location.hash !== '#cases') {
              window.history.replaceState({ isLanding: false, tab: 'cases', accountId: '' }, '', '#cases');
            }
          } else {
            setIsLanding(true);
          }
        } else if (tab === 'app' || tab === 'overview') {
          setIsLanding(false);
          setActiveTab('cases');
          setSelectedAccountId(acc || '');
          window.history.replaceState({ isLanding: false, tab: 'cases', accountId: acc || '' }, '', '#cases');
        } else if (['cases', 'investigate', 'documents', 'dataset-status'].includes(tab)) {
          setIsLanding(false);
          setActiveTab(tab as ActiveTab);
          setSelectedAccountId(acc || '');
        }
      } else {
        if (isAuth) {
          setIsLanding(false);
          setActiveTab('cases');
          setSelectedAccountId('');
          window.history.replaceState({ isLanding: false, tab: 'cases', accountId: '' }, '', '#cases');
        } else {
          setIsLanding(true);
        }
      }
    };

    const handlePopState = (e: PopStateEvent) => {
      const isAuth = checkIsAuth();
      if (isAuth) {
        // Prevent browser back from exiting to landing or external browser history
        if (!window.location.hash || window.location.hash === '#' || window.location.hash === '#landing' || (e.state && e.state.isLanding)) {
          setIsLanding(false);
          setActiveTab('cases');
          setSelectedAccountId('');
          setHistoryStack([]);
          window.history.pushState({ isLanding: false, tab: 'cases', accountId: '' }, '', '#cases');
          return;
        }

        if (e.state && typeof e.state.isLanding === 'boolean' && !e.state.isLanding) {
          setIsLanding(false);
          if (e.state.tab) setActiveTab(e.state.tab === 'overview' ? 'cases' : e.state.tab);
          if (e.state.accountId !== undefined) setSelectedAccountId(e.state.accountId);
          setHistoryStack((prev) => (prev.length > 0 ? prev.slice(0, -1) : []));
          return;
        }
      }

      handleUrlHash();
    };

    window.addEventListener('popstate', handlePopState);
    window.addEventListener('hashchange', handleUrlHash);
    return () => {
      window.removeEventListener('popstate', handlePopState);
      window.removeEventListener('hashchange', handleUrlHash);
    };
  }, []);

  // Alt + ArrowLeft keyboard shortcut for going back
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.altKey && e.key === 'ArrowLeft') {
        const target = e.target as HTMLElement;
        const isInput = target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
        if (!isInput) {
          e.preventDefault();
          handleGoBack();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [historyStack, activeTab, selectedAccountId, isLanding]);

  // Check backend health periodically
  useEffect(() => {
    const check = async () => {
      try {
        const h = await api.checkHealth();
        setBackendOnline(h.status === 'ok');
        const ds = await api.getDatasetStatus().catch(() => null);
        if (ds) setDatasetId(ds.dataset_id);
      } catch {
        setBackendOnline(false);
      }
    };
    check();
    const interval = setInterval(check, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleLaunchInvestigation = () => {
    // Open authentication modal with 3 dummy profiles right after launch button click
    setIsAuthOpen(true);
  };

  const handleAuthenticated = (selectedOfficer: OfficerProfile) => {
    setOfficer(selectedOfficer);
    try {
      localStorage.setItem('anvitrace_officer', JSON.stringify(selectedOfficer));
    } catch {}
    setIsAuthOpen(false);
    setIsLanding(false);
    setActiveTab('cases');
    setSelectedAccountId('');
    setHistoryStack([]);
    if (typeof window !== 'undefined') {
      window.history.replaceState({ isLanding: false, tab: 'cases', accountId: '' }, '', '#cases');
      window.history.pushState({ isLanding: false, tab: 'cases', accountId: '' }, '', '#cases');
    }
  };

  const handleLogout = () => {
    try {
      localStorage.removeItem('anvitrace_officer');
    } catch {}
    setOfficer(null);
    setIsLanding(true);
    setSelectedAccountId('');
    setHistoryStack([]);
    if (typeof window !== 'undefined') {
      window.history.replaceState({ isLanding: true }, '', '#landing');
    }
  };

  const handleNavigateToLanding = () => {
    setIsLanding(true);
    if (typeof window !== 'undefined') {
      window.history.pushState({ isLanding: true }, '', '#landing');
    }
  };

  const navigateTo = (tab: ActiveTab, accountId: string = '', addToHistory: boolean = true) => {
    setIsLanding(false);
    if (tab === activeTab && accountId === selectedAccountId && !isLanding) return;

    if (addToHistory) {
      setHistoryStack((prev) => [
        ...prev,
        { tab: activeTab, accountId: selectedAccountId },
      ]);
      if (typeof window !== 'undefined') {
        const hash = `#${tab}${accountId ? `/${accountId}` : ''}`;
        window.history.pushState({ isLanding: false, tab, accountId }, '', hash);
      }
    }

    setActiveTab(tab);
    setSelectedAccountId(accountId);
  };

  const handleGoBack = () => {
    if (isLanding) return;

    if (historyStack.length > 0) {
      const prev = historyStack[historyStack.length - 1];
      setHistoryStack((stack) => stack.slice(0, -1));
      setActiveTab(prev.tab === 'overview' ? 'cases' : prev.tab);
      setSelectedAccountId(prev.accountId || '');
      if (typeof window !== 'undefined') {
        const effectiveTab = prev.tab === 'overview' ? 'cases' : prev.tab;
        const hash = `#${effectiveTab}${prev.accountId ? `/${prev.accountId}` : ''}`;
        window.history.replaceState({ isLanding: false, tab: effectiveTab, accountId: prev.accountId }, '', hash);
      }
    } else if (activeTab !== 'cases' || Boolean(selectedAccountId)) {
      // Revert back till Cases & Evidence main screen
      setActiveTab('cases');
      setSelectedAccountId('');
      setHistoryStack([]);
      if (typeof window !== 'undefined') {
        window.history.replaceState({ isLanding: false, tab: 'cases', accountId: '' }, '', '#cases');
      }
    }
    // If already on cases with no account and empty history, stop!
    // Never drop back to landing screen or exit to browser history.
  };

  const handleSelectAccount = (acc: string) => {
    navigateTo('investigate', acc);
  };

  const handleNavigateToNotice = (acc: string, docType?: 'freeze_requisition' | 'case_diary') => {
    if (docType) {
      setSelectedDocType(docType);
    }
    navigateTo('documents', acc);
  };

  const handleTabChange = (tab: ActiveTab) => {
    navigateTo(tab === 'overview' ? 'cases' : tab, '');
  };

  const canGoBack = !isLanding && (activeTab !== 'cases' || Boolean(selectedAccountId) || historyStack.length > 0);

  if (isLanding) {
    return (
      <div className="w-full h-screen overflow-y-auto overflow-x-hidden bg-[#fbfbfd]">
        <LandingPage onLaunchInvestigation={handleLaunchInvestigation} />
        <AuthModal
          isOpen={isAuthOpen}
          onClose={() => setIsAuthOpen(false)}
          onAuthenticate={handleAuthenticated}
        />
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900 antialiased overflow-hidden selection:bg-amber-100 selection:text-amber-900">
      {/* Mobile Backdrop Overlay when sidebar is open on screens < lg */}
      {sidebarOpen && (
        <div
          onClick={toggleSidebar}
          className="fixed inset-0 bg-slate-950/60 backdrop-blur-xs z-40 lg:hidden transition-opacity duration-300 cursor-pointer"
          aria-hidden="true"
        />
      )}

      {/* Sidebar Navigation matching user requested core stages */}
      <Sidebar
        isOpen={sidebarOpen}
        onToggleSidebar={toggleSidebar}
        activeTab={activeTab === 'overview' ? 'cases' : activeTab}
        setActiveTab={(tab) => {
          handleTabChange(tab);
          if (typeof window !== 'undefined' && window.innerWidth < 1024) {
            setSidebarOpen(false);
          }
        }}
        backendOnline={backendOnline}
        officer={officer}
        onLogout={handleLogout}
      />

      {/* Main Content Pane with smooth margin transition */}
      <div
        className={`flex-1 flex flex-col h-screen overflow-hidden bg-slate-50 transition-all duration-300 ease-in-out ${
          sidebarOpen ? 'lg:ml-72' : 'ml-0'
        }`}
      >
        {/* Sticky Header with Hamburger Toggle */}
        <Header
          isSidebarOpen={sidebarOpen}
          onToggleSidebar={toggleSidebar}
          caseReference={datasetId ? `DATASET: ${datasetId.slice(0, 10)}` : 'AIR-GAP FORENSICS'}
          datasetId={datasetId}
          backendOnline={backendOnline}
          onSearchTarget={handleSelectAccount}
          canGoBack={canGoBack}
          onGoBack={handleGoBack}
          officer={officer}
          onLogout={handleLogout}
        />

        {/* Scrollable View Area */}
        <main className="flex-1 overflow-y-auto px-8 py-6 bg-slate-50">
          {/* 1. Create Case & Upload Evidence (direct landing section on login) */}
          {(activeTab === 'cases' || activeTab === 'overview') && (
            <CaseManagementView
              onInvestigateAccount={handleSelectAccount}
              onNavigateToNotice={handleNavigateToNotice}
              onNavigateToDataset={() => navigateTo('dataset-status')}
              onNavigateToDocuments={() => navigateTo('documents')}
              onGoBack={handleGoBack}
            />
          )}

          {/* 3. Account Deep-Dive: Search ID, Risk Score & Breakdown, Inflow/Outflow Lineage, SHA-256 Check */}
          {activeTab === 'investigate' && (
            <InvestigateWorkspace
              initialAccountId={selectedAccountId}
              onNavigateToNotice={handleNavigateToNotice}
              onGoBack={handleGoBack}
              onAccountChange={(newAcc) => {
                if (newAcc !== selectedAccountId) {
                  navigateTo('investigate', newAcc);
                }
              }}
            />
          )}

          {/* 4. Bank Freeze Notice & Case Diary Generator: Enter ID -> PDF format */}
          {activeTab === 'documents' && (
            <DocumentsView
              initialAccountId={selectedAccountId}
              initialDocType={selectedDocType}
              onInvestigateAccount={handleSelectAccount}
              onGoBack={handleGoBack}
            />
          )}

          {/* Advanced / Technical Ingestion Diagnostics */}
          {activeTab === 'dataset-status' && (
            <DatasetView onGoBack={handleGoBack} />
          )}
        </main>
      </div>

      {/* Auth Switcher Modal accessible within dashboard */}
      <AuthModal
        isOpen={isAuthOpen}
        onClose={() => setIsAuthOpen(false)}
        onAuthenticate={handleAuthenticated}
      />
    </div>
  );
};

export default App;
