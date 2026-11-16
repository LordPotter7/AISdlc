import { useEffect } from "react";
import { getTransport } from "@/lib/ipc";
import { applyDensity, applyTheme, useStudio } from "@/state/store";
import { CommandPalette } from "@/components/CommandPalette";
import { FileViewer } from "@/components/FileViewer";
import { GuideProvider } from "@/components/Guide";
import { NewIntentDialog } from "@/components/NewIntentDialog";
import { Breadcrumbs, ShortcutSheet, Sidebar, StatusBar, TopBar, useShortcuts } from "@/components/Shell";
import { Toaster } from "@/components/Toaster";
import { ViewBoundary } from "@/components/ViewBoundary";
import { CockpitView } from "@/views/CockpitView";
import { CodebaseView } from "@/views/CodebaseView";
import { PipelineView } from "@/views/PipelineView";
import { PortfolioView } from "@/views/PortfolioView";
import { DoctorView } from "@/views/DoctorView";
import { InboxView } from "@/views/InboxView";
import { IntentView } from "@/views/IntentView";
import { LearnView } from "@/views/LearnView";
import { ProjectsView } from "@/views/ProjectsView";
import { StoryView } from "@/views/StoryView";

/** Surfaces that float above every view: search, shortcuts, the file panel, and toasts. */
function Overlays() {
  const newIntentOpen = useStudio((s) => s.newIntentOpen);
  const setOverlay = useStudio((s) => s.setOverlay);
  return (
    <>
      <FileViewer />
      <CommandPalette />
      <ShortcutSheet />
      {newIntentOpen && <NewIntentDialog onClose={() => setOverlay("newIntent", false)} />}
      <Toaster />
    </>
  );
}

export default function App() {
  const route = useStudio((s) => s.route);
  const project = useStudio((s) => s.project);
  const error = useStudio((s) => s.error);
  const clearError = useStudio((s) => s.clearError);
  const theme = useStudio((s) => s.theme);
  const density = useStudio((s) => s.density);
  const collapsed = useStudio((s) => s.sidebarCollapsed);
  useShortcuts();

  useEffect(() => applyTheme(theme), [theme]);
  useEffect(() => applyDensity(density), [density]);

  useEffect(() => {
    const transport = getTransport();
    const state = useStudio.getState();
    let disposed = false;
    const unlisten: (() => void)[] = [];
    void transport.onNotification((note) => useStudio.getState().applyNotification(note)).then((u) => (disposed ? u() : unlisten.push(u)));
    void transport.onStatus((status) => useStudio.getState().setBridge(status)).then((u) => (disposed ? u() : unlisten.push(u)));
    void transport.bridgeStatus().then(state.setBridge).catch(() => undefined);
    void state.loadRegistry().then(() => {
      const { registry, project: open } = useStudio.getState();
      // Reopen the most recently used repository; in the browser preview, the recorded demo.
      const last = [...registry].sort((a, b) => (b.last_opened ?? "").localeCompare(a.last_opened ?? ""))[0];
      if (!open && last && !last.missing) void useStudio.getState().open(last);
    });
    return () => {
      disposed = true;
      unlisten.forEach((u) => u());
    };
  }, []);

  const story = route.view === "story" && !!project;
  return (
    <GuideProvider>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className={`shell${story ? " story-shell" : ""}`}>
        <TopBar />
        <div className={`body${collapsed ? " nav-collapsed" : ""}`}>
          <Sidebar />
          <main className="main" id="main" tabIndex={-1} style={story ? { overflow: "hidden" } : undefined}>
            {story ? (
              <ViewBoundary key={`story-${route.intentId ?? ""}`}>
                <StoryView intentId={route.intentId} />
              </ViewBoundary>
            ) : (
              <>
                <Breadcrumbs />
                {error && (
                  <div className="page" style={{ paddingBottom: 0 }}>
                    <div className="error-box" role="alert">
                      {error}{" "}
                      <button type="button" className="btn sm" onClick={clearError}>
                        Dismiss
                      </button>
                    </div>
                  </div>
                )}
                <ViewBoundary key={`${route.view}-${route.intentId ?? ""}-${route.tab ?? ""}`}>
                  {(!project || route.view === "projects") && <ProjectsView />}
                  {project && route.view === "learn" && <LearnView />}
                  {project && route.view === "inbox" && <InboxView />}
                  {project && route.view === "cockpit" && <CockpitView />}
                  {project && route.view === "portfolio" && <PortfolioView />}
                  {project && route.view === "codebase" && <CodebaseView />}
                  {project && route.view === "intent" && route.intentId && (
                    <IntentView key={route.intentId} intentId={route.intentId} tab={route.tab ?? "overview"} />
                  )}
                  {project && route.view === "doctor" && <DoctorView />}
                  {project && route.view === "pipeline" && <PipelineView />}
                </ViewBoundary>
              </>
            )}
          </main>
        </div>
        {!story && <StatusBar />}
      </div>
      <Overlays />
    </GuideProvider>
  );
}
