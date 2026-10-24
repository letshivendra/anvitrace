import React from 'react';
import {
  FolderArchive,
  ScanSearch,
  FileCheck2,
  Cpu,
  LogOut,
  PanelLeftClose,
} from 'lucide-react';
import type { OfficerProfile } from './AuthModal';

export type ActiveTab =
  | 'overview'
  | 'cases'
  | 'investigate'
  | 'documents'
  | 'dataset-status';

interface SidebarProps {
  activeTab: ActiveTab;
  setActiveTab: (tab: ActiveTab) => void;
  backendOnline?: boolean;
  officer?: OfficerProfile | null;
  onLogout?: () => void;
  isOpen?: boolean;
  onToggleSidebar?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  setActiveTab,
  officer,
  onLogout,
  isOpen = true,
  onToggleSidebar,
}) => {
  const navItems = [
    {
      id: 'cases' as const,
      label: 'Cases & Evidence',
      icon: FolderArchive,
      description: 'Register case & upload CSV',
    },
    {
      id: 'investigate' as const,
      label: 'Account Deep-Dive',
      icon: ScanSearch,
      description: 'Search ID, score & lineage',
    },
    {
      id: 'documents' as const,
      label: 'Legal Documents',
      icon: FileCheck2,
      description: 'Sec. 102 Notice & Rule 172 Diary',
    },
  ];

  return (
    <aside
      className={`fixed left-0 top-0 h-screen w-72 bg-white/95 dark:bg-[#0f172a]/95 backdrop-blur-md z-50 flex flex-col justify-between border-r border-slate-200 dark:border-slate-800 shadow-[2px_0_12px_rgba(15,23,42,0.03)] select-none transition-transform duration-300 ease-in-out ${
        isOpen ? 'translate-x-0' : '-translate-x-full'
      }`}
    >
      <div className="flex flex-col flex-1 min-h-0">
        {/* Brand Header */}
        <div className="p-4 sm:p-5 border-b border-slate-100 dark:border-slate-800/80 bg-white dark:bg-[#0f172a]">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-10 h-10 rounded-xl bg-white dark:bg-slate-800 border border-slate-200/90 dark:border-slate-700 p-1 flex items-center justify-center shadow-xs shrink-0 overflow-hidden">
                <img src="/anvitrace-icon.png" alt="ANVITRACE Logo" className="w-full h-full object-contain" />
              </div>
              <div className="flex flex-col min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="font-extrabold text-base tracking-tight text-slate-950 dark:text-white font-['Plus_Jakarta_Sans',sans-serif]">
                    ANVITRACE
                  </span>
                  <span className="text-[9px] font-bold tracking-wider px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-950/60 text-amber-800 dark:text-amber-400 border border-amber-200 dark:border-amber-800/60">
                    v2.0
                  </span>
                </div>
                <span className="text-[11px] text-slate-500 dark:text-slate-400 font-medium truncate">
                  Cyber Forensics &amp; Audit
                </span>
              </div>
            </div>

            {onToggleSidebar && (
              <button
                type="button"
                onClick={onToggleSidebar}
                className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer shrink-0"
                title="Collapse sidebar (Ctrl+B)"
                aria-label="Collapse sidebar"
                id="sidebar-collapse-btn"
              >
                <PanelLeftClose size={18} />
              </button>
            )}
          </div>

          <div className="mt-3 px-2.5 py-1.5 rounded-lg bg-slate-50 dark:bg-slate-800/70 border border-slate-100 dark:border-slate-700/60 flex items-center justify-between text-[11px] text-slate-600 dark:text-slate-300">
            <span className="font-medium text-slate-500 dark:text-slate-400">Mule Detection Engine</span>
            <span className="font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-1">
              <Cpu size={12} className="text-amber-600 dark:text-amber-400" /> 4-Hop BFS
            </span>
          </div>
        </div>

        {/* Minimal Navigation */}
        <div className="px-3 pt-4 pb-2">
          <span className="px-3 text-[10px] font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">
            FORENSIC WORKSPACES
          </span>
        </div>

        <nav className="flex-1 px-3 space-y-1.5 overflow-y-auto">
          {navItems.map((item) => {
            const isActive = activeTab === item.id;
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-left transition-all duration-150 cursor-pointer ${
                  isActive
                    ? 'bg-slate-900 dark:bg-amber-500/15 text-white dark:text-amber-300 font-medium shadow-sm dark:border dark:border-amber-500/30'
                    : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100/80 dark:hover:bg-slate-800/80 hover:text-slate-900 dark:hover:text-slate-100'
                }`}
              >
                <div
                  className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-colors ${
                    isActive
                      ? 'bg-slate-800 dark:bg-amber-400/20 text-slate-100 dark:text-amber-300'
                      : 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400'
                  }`}
                >
                  <Icon size={17} />
                </div>
                <div className="flex flex-col min-w-0">
                  <span className="text-[13px] font-semibold tracking-tight leading-tight">
                    {item.label}
                  </span>
                  <span
                    className={`text-[11px] truncate leading-tight mt-0.5 ${
                      isActive ? 'text-slate-400 dark:text-amber-400/80' : 'text-slate-500 dark:text-slate-400'
                    }`}
                  >
                    {item.description}
                  </span>
                </div>
              </button>
            );
          })}
        </nav>
      </div>

      {/* Bottom Officer Session & Sign Out */}
      <div className="p-3.5 border-t border-slate-100 dark:border-slate-800/80 bg-slate-50/70 dark:bg-slate-900/80 flex items-center justify-between gap-2">
        {officer ? (
          <div className="flex items-center gap-2.5 min-w-0">
            <div
              className={`w-7 h-7 rounded-lg flex items-center justify-center font-mono font-bold text-[10px] shrink-0 ${officer.themeColor}`}
            >
              {officer.initials}
            </div>
            <div className="flex flex-col min-w-0">
              <span className="text-[11px] font-bold text-slate-800 dark:text-slate-200 truncate leading-tight">
                {officer.name.split(' ')[0]} {officer.name.split(' ')[1]}
              </span>
              <span className="text-[9px] font-mono text-slate-500 dark:text-slate-400 leading-tight">
                #{officer.badge}
              </span>
            </div>
          </div>
        ) : (
          <div className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Session Active</div>
        )}

        {onLogout && (
          <button
            type="button"
            onClick={onLogout}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-slate-600 dark:text-slate-400 hover:text-red-700 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40 border border-slate-200 dark:border-slate-700 hover:border-red-200 dark:hover:border-red-800 text-xs font-semibold transition-all cursor-pointer shadow-2xs group shrink-0"
            title="Log out and return to landing page"
            id="sidebar-logout-button"
          >
            <LogOut size={13} className="text-slate-400 dark:text-slate-400 group-hover:text-red-600 dark:group-hover:text-red-400 transition-colors" />
            <span>Log Out</span>
          </button>
        )}
      </div>
    </aside>
  );
};
