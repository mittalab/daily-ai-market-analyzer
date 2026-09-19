import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
  fallbackMessage?: string;
  onReset?: () => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
  }

  private handleReset = () => {
    this.setState({ hasError: false, error: null });
    if (this.props.onReset) {
      this.props.onReset();
    }
  };

  public render() {
    if (this.state.hasError) {
      return (
        <div className="p-4 m-2 bg-red-50 border border-red-200 rounded-xl text-red-800 text-sm">
          <div className="flex items-center justify-between mb-2">
            <span className="font-semibold text-red-900">
              {this.props.fallbackTitle ?? 'Something went wrong rendering this component'}
            </span>
            <button
              onClick={this.handleReset}
              className="text-xs font-medium px-2.5 py-1 bg-red-100 hover:bg-red-200 border border-red-300 rounded text-red-900 transition-colors"
            >
              Try again
            </button>
          </div>
          <p className="text-xs text-red-700">
            {this.props.fallbackMessage ?? this.state.error?.message ?? 'An unexpected error occurred.'}
          </p>
        </div>
      );
    }

    return this.props.children;
  }
}
