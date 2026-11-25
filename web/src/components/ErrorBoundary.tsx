import React from 'react';

interface ErrorBoundaryProps {
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    if (import.meta.env.DEV) {
      console.error('Unhandled UI error caught by ErrorBoundary:', error, info.componentStack);
    }
  }

  handleReload = (): void => {
    window.location.reload();
  };

  handleHome = (): void => {
    window.location.assign('/');
  };

  render(): React.ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <div
        style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: 'var(--paper)',
          color: 'var(--ink)',
          padding: 24,
        }}
      >
        <div
          className="card card-pad"
          role="alert"
          style={{
            maxWidth: 520,
            width: '100%',
            borderLeft: '3px solid var(--red)',
          }}
        >
          <div className="section-kicker" style={{ color: 'var(--red)', marginBottom: 6 }}>
            AI Watchdogs India
          </div>
          <h1 className="font-serif" style={{ margin: '0 0 10px', fontSize: 26, lineHeight: 1.25 }}>
            Something went wrong while rendering this view
          </h1>
          <p style={{ margin: '0 0 18px', fontSize: 14.5, lineHeight: 1.55, color: 'var(--ink-2)' }}>
            An unexpected display error occurred. Reload the page or return to the home view to
            continue inspecting the public audit record.
          </p>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button type="button" className="btn btn-primary btn-sm" onClick={this.handleReload}>
              Reload page
            </button>
            <button type="button" className="btn btn-secondary btn-sm" onClick={this.handleHome}>
              Return to home
            </button>
          </div>
        </div>
      </div>
    );
  }
}
