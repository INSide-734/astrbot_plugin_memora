import { Component, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  private static _t(key: string): string {
    const t = (window as any).t as ((k: string) => string) | undefined;
    return t?.(key) ?? key;
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error("[ErrorBoundary]", error.message, info.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;

      return (
        <div className="flex flex-col items-center justify-center h-full gap-4 p-8">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-danger)]/10">
            <AlertTriangle size={24} className="text-[var(--color-danger)]" />
          </div>
          <div className="text-center space-y-1">
            <p className="text-sm font-medium text-[var(--text-primary)]">{ErrorBoundary._t("error.somethingWentWrong")}</p>
            <p className="text-xs text-[var(--text-tertiary)] max-w-md break-all">
              {this.state.error?.message ?? ErrorBoundary._t("error.unknown")}
            </p>
          </div>
          <button
            onClick={this.handleRetry}
            className="inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 transition-opacity"
          >
            <RefreshCw size={14} />
            {ErrorBoundary._t("common.retry")}
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
