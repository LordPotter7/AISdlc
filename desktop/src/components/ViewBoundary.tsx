import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

/**
 * A view that fails renders an explanation in its place; the shell, navigation, and
 * other views keep working. Reset by navigating (the boundary is keyed by route).
 */
export class ViewBoundary extends Component<{ children: ReactNode }, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("view failed", error, info.componentStack);
  }

  override render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page">
        <div className="error-box" role="alert">
          This view could not be displayed: {this.state.error.message}. The rest of Studio still works; choose another view or
          refresh.
        </div>
      </div>
    );
  }
}
