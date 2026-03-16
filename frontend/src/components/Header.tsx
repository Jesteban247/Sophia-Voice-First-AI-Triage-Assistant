import { ConnectionStatus } from '../types';

interface HeaderProps {
  status: ConnectionStatus;
  isConnected: boolean;
  isConnecting: boolean;
  onToggleConnection: () => void;
}

export function Header({ status, isConnected, isConnecting, onToggleConnection }: HeaderProps) {
  const connectionLabel = isConnecting ? 'Connecting…' : status === 'recording' ? 'Listening' : isConnected ? 'Connected' : 'Disconnected';
  const isActive = status === 'recording';
  return (
    <header className="header">
      <div className="header-title">
        Medical Triage
        <small>Tiffany · Nova Sonic 2</small>
      </div>

      <div className="header-orb">
        <input type="checkbox" id="toggle" checked={isConnected} readOnly />
        <label
          htmlFor="toggle"
          className={`orb-button${isActive ? ' active' : ''}${isConnecting ? ' connecting' : ''}${isConnecting ? ' loading' : ''}`}
          onClick={e => {
            e.preventDefault();
            if (!isConnecting) onToggleConnection();
          }}
          title={isConnected ? 'End session' : 'Start session'}
        >
          <div className="orb">
            <svg xmlns="http://www.w3.org/2000/svg" fill="currentColor" className="bi bi-mic-fill" viewBox="0 0 16 16">
              <path d="M5 3a3 3 0 0 1 6 0v5a3 3 0 0 1-6 0z" />
              <path d="M3.5 6.5A.5.5 0 0 1 4 7v1a4 4 0 0 0 8 0V7a.5.5 0 0 1 1 0v1a5 5 0 0 1-4.5 4.975V15h3a.5.5 0 0 1 0 1h-7a.5.5 0 0 1 0-1h3v-2.025A5 5 0 0 1 3 8V7a.5.5 0 0 1 .5-.5" />
            </svg>
          </div>
          <div className="waveform">
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="bar" />
            ))}
          </div>
        </label>
        <div className="orb-status">{connectionLabel}</div>
      </div>

    </header>
  );
}
