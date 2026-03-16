import { useRef, RefObject as ReactRefObject, KeyboardEvent, useMemo } from 'react';
import { ChatMessage, PendingMedia } from '../types';

interface ConversationPanelProps {
  messages: ChatMessage[];
  messagesEndRef: ReactRefObject<HTMLDivElement>;
  isConnected: boolean;
  pendingMedia: PendingMedia | null;
  requestMediaPulsing: boolean;
  onSendText: (text: string) => void;
  onMediaUpload: (file: File) => void;
}

export function ConversationPanel({
  messages,
  messagesEndRef,
  isConnected,
  pendingMedia,
  requestMediaPulsing,
  onSendText,
  onMediaUpload,
}: ConversationPanelProps) {
  const textRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const lastUser = useMemo(
    () => [...messages].reverse().find(m => m.type === 'user')?.content ?? '—',
    [messages]
  );
  const lastAssistant = useMemo(
    () => [...messages].reverse().find(m => m.type === 'assistant')?.content ?? '—',
    [messages]
  );

  const handleSend = () => {
    const val = textRef.current?.value.trim() ?? '';
    onSendText(val);
    if (textRef.current) textRef.current.value = '';
  };

  const handleKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSend();
  };

  const handleFileChange = () => {
    const file = fileRef.current?.files?.[0];
    if (file) {
      onMediaUpload(file);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <div className="panel transcript-panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">Live Transcript</div>
          <div className="panel-subtitle">Auto-captured from speech and tool events</div>
        </div>
        <div className={`status-pill ${isConnected ? 'on' : 'off'}`}>
          {isConnected ? 'Listening' : 'Offline'}
        </div>
      </div>

      <div className="speaking-strip">
        <div className="speaking-row">
          <span className="speaking-label">You</span>
          <span className="speaking-text">{lastUser}</span>
        </div>
        <div className="speaking-row">
          <span className="speaking-label">AI</span>
          <span className="speaking-text">{lastAssistant}</span>
        </div>
      </div>

      <div className="transcript-scroll">
        {messages.map(msg => (
          <div key={msg.id} className={`transcript-row ${msg.type}`}>
            <span className="transcript-role">{msg.type}</span>
            <span className="transcript-text">{msg.content}</span>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="input-bar">
        <input
          ref={textRef}
          type="text"
          className="text-input"
          placeholder={
            pendingMedia
              ? `Image ready — press Send to analyze`
              : isConnected
              ? 'Type a message…'
              : 'Connect first to start chatting'
          }
          disabled={!isConnected}
          onKeyDown={handleKey}
        />

        {/* Hidden file input */}
        <input
          ref={fileRef}
          type="file"
          accept="image/*,video/*"
          style={{ display: 'none' }}
          onChange={handleFileChange}
          disabled={!isConnected}
        />

        <button
          className={`btn btn-attach${requestMediaPulsing ? ' pulse' : ''}`}
          disabled={!isConnected}
          onClick={() => fileRef.current?.click()}
          title="Attach photo"
        >
          Attach
        </button>

        <button
          className="btn btn-send"
          disabled={!isConnected}
          onClick={handleSend}
        >
          Send
        </button>
      </div>

    </div>
  );
}
