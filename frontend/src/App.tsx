import { useState, useRef, useCallback, useEffect } from 'react';
import { ChatMessage, ConnectionStatus, PendingMedia, TriageState } from './types';
import { useAudioPlayback } from './hooks/useAudioPlayback';
import { useAudioRecorder } from './hooks/useAudioRecorder';
import { Header } from './components/Header';
import { ConversationPanel } from './components/ConversationPanel';
import { PatientDashboard } from './components/PatientDashboard';
import { VitalsModal } from './components/VitalsModal';

const EMPTY_TRIAGE: TriageState = {
  stage: 'identity',
  identity: {},
  clinical: {},
  images: [],
};

let msgIdCounter = 0;
function makeMsg(type: ChatMessage['type'], content: string): ChatMessage {
  return { id: String(++msgIdCounter), type, content, timestamp: Date.now() };
}

export default function App() {
  const [status, setStatus] = useState<ConnectionStatus>('disconnected');
  const wsRef = useRef<WebSocket | null>(null);
  const sessionStartedRef = useRef(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [triage, setTriage] = useState<TriageState>(EMPTY_TRIAGE);
  const [reportUrl, setReportUrl] = useState<string | null>(null);
  const [requestMediaPulsing, setRequestMediaPulsing] = useState(false);
  const [showVitalsScan, setShowVitalsScan] = useState(false);
  const [vitalsResults, setVitalsResults] = useState<{ hr: number | null; rr: number | null } | null>(null);
  const [pendingMedia, setPendingMedia] = useState<PendingMedia | null>(null);
  const currentStageRef = useRef<string | null>(null);
  const isInterruptedRef = useRef(false);
  const vitalsCompletedRef = useRef(false);
  const transcriptBuf = useRef({
    user: { text: '', timer: null as ReturnType<typeof setTimeout> | null, msgId: null as string | null },
    assistant: { text: '', timer: null as ReturnType<typeof setTimeout> | null, msgId: null as string | null },
  });
  const { playAudio, stopAllAudio } = useAudioPlayback();

  const handleAudioChunk = useCallback((base64: string) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && sessionStartedRef.current) {
      ws.send(JSON.stringify({ action: 'audio', content: base64 }));
    }
  }, []);

  const { isRecording, startRecording, stopRecording } = useAudioRecorder({
    onAudioChunk: handleAudioChunk,
  });
  const addMsg = useCallback((type: ChatMessage['type'], content: string) => {
    const msg = makeMsg(type, content);
    setMessages(prev => [...prev, msg]);
    return msg.id;
  }, []);

  const updateMsgById = useCallback((id: string, content: string) => {
    setMessages(prev => prev.map(m => m.id === id ? { ...m, content } : m));
  }, []);
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);
  const handleBufferedTranscript = useCallback((role: 'user' | 'assistant', text: string) => {
    const buf = transcriptBuf.current[role];
    if (buf.timer) clearTimeout(buf.timer);

    buf.text += (buf.text ? ' ' : '') + text;
    const prefix = role === 'user' ? '🎤 User' : '🔊 Assistant';
    const content = `${prefix}: ${buf.text}`;

    if (buf.msgId) {
      updateMsgById(buf.msgId, content);
    } else {
      const id = addMsg(role === 'user' ? 'user' : 'assistant', content);
      buf.msgId = id;
    }

    buf.timer = setTimeout(() => {
      buf.text = '';
      buf.msgId = null;
      buf.timer = null;
    }, 1000);
  }, [addMsg, updateMsgById]);

  const clearTranscriptBuffers = useCallback(() => {
    const buf = transcriptBuf.current;
    (['user', 'assistant'] as const).forEach(r => {
      if (buf[r].timer) clearTimeout(buf[r].timer);
      buf[r] = { text: '', timer: null, msgId: null };
    });
  }, []);
  const handleBargeIn = useCallback(() => {
    isInterruptedRef.current = true;
    addMsg('system', '🚫 [Interrupted — barge-in detected]');
    clearTranscriptBuffers();
    stopAllAudio();
  }, [addMsg, clearTranscriptBuffers, stopAllAudio]);
  const uploadToS3 = useCallback(async (file: File): Promise<string> => {
    const res = await fetch('/api/s3-upload-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, contentType: file.type }),
    });
    if (!res.ok) throw new Error(`Failed to get upload URL: ${res.statusText}`);
    const data = await res.json();

    const upload = await fetch(data.uploadUrl, {
      method: 'PUT',
      body: file,
      headers: { 'Content-Type': file.type },
    });
    if (!upload.ok) throw new Error(`S3 upload failed: ${upload.statusText}`);
    return data.s3Uri as string;
  }, []);
  const canvasToFile = (canvasId: string, filename: string): Promise<File> =>
    new Promise((resolve, reject) => {
      let canvas = document.getElementById(canvasId) as HTMLCanvasElement | null;
      if (!canvas) {
        const monitor = document.querySelector('vitallens-monitor') as HTMLElement | null;
        const root = (monitor as any)?.shadowRoot as ShadowRoot | undefined;
        const canvases = root ? Array.from(root.querySelectorAll('canvas')) as HTMLCanvasElement[] : [];
        if (canvases.length >= 2) {
          canvas = canvasId.includes('hr') ? canvases[0] : canvases[1];
        }
      }
      if (!canvas) {
        reject(new Error('Chart canvas not found'));
        return;
      }
      canvas.toBlob(blob => resolve(new File([blob!], filename, { type: 'image/png' })), 'image/png');
    });
  const handleWsMessage = useCallback(async (event: MessageEvent) => {
    const data = JSON.parse(event.data as string);
    if (!data.event) return;

    const eventType = Object.keys(data.event)[0] as string;
    const eventData = data.event[eventType] as Record<string, unknown>;

    switch (eventType) {
      case 'ready':
        addMsg('system', '✅ Session ready — starting microphone');
        sessionStartedRef.current = true;
        setStatus('connected');
        try {
          await startRecording();
          setStatus('recording');
        } catch {
          addMsg('system', '❌ Microphone access denied');
        }
        break;

      case 'requestMedia':
        addMsg('system', '📸 ' + ((eventData.message as string | undefined) || 'Please send a photo of the affected area'));
        setRequestMediaPulsing(true);
        setTimeout(() => setRequestMediaPulsing(false), 5000);
        break;

      case 'startVitals':
        addMsg('system', '💓 Starting 30-second vital signs scan — please look at the camera and stay still');
        setShowVitalsScan(true);
        break;

      case 'reportReady':
        setReportUrl(eventData.url as string);
        addMsg('system', '✅ Triage report is ready — see the dashboard →');
        break;

      case 'contentStart': {
        if (eventData.additionalModelFields) {
          try {
            const mf = JSON.parse(eventData.additionalModelFields as string) as { generationStage?: string };
            currentStageRef.current = mf.generationStage ?? null;
          } catch { /* ignore */ }
        }
        if ((eventData.role as string) === 'ASSISTANT' && (eventData.type as string) === 'AUDIO') {
          isInterruptedRef.current = false;
        }
        break;
      }

      case 'contentEnd': {
        const stopReason = ((eventData.stopReason as string | undefined) || '').toUpperCase();
        if (stopReason === 'INTERRUPTED' || stopReason === 'INTERRUPTION') {
          handleBargeIn();
        }
        currentStageRef.current = null;
        break;
      }

      case 'interruption':
        handleBargeIn();
        break;

      case 'audioOutput':
        if (!isInterruptedRef.current) {
          await playAudio(eventData.content as string, 24000);
        }
        break;

      case 'textOutput': {
        const role = ((eventData.role as string | undefined) || 'ASSISTANT').toLowerCase() as 'user' | 'assistant';
        const shouldDisplay =
          (role === 'assistant' && currentStageRef.current === 'SPECULATIVE') ||
          (role === 'user' && currentStageRef.current === 'FINAL');
        if (shouldDisplay) {
          handleBufferedTranscript(role, eventData.content as string);
        }
        break;
      }

      case 'toolUse':
        addMsg('tool', `🔧 Tool: ${eventData.toolName as string}`);
        break;

      case 'toolResult': {
        try {
          const outer = JSON.parse(eventData.content as string) as { result?: string };
          const result = JSON.parse(outer.result || '{}') as {
            session_state?: TriageState;
            status?: string;
            disconnect_delay_seconds?: number;
            action?: string;
          };
          if (result.status === 'disconnecting' && result.action === 'close_connection') {
            const delay = result.disconnect_delay_seconds || 7;
            addMsg('system', '👋 Disconnecting...');
            setTimeout(() => {
              disconnect();
            }, delay * 1000);
          } else if (result.session_state) {
            setTriage(result.session_state);
          }
        } catch { /* ignore */ }
        break;
      }

      default:
        break;
    }
  }, [addMsg, handleBargeIn, handleBufferedTranscript, playAudio, startRecording]);
  const connect = useCallback(async () => {
    setStatus('connecting');
    setMessages([]);
    setTriage(EMPTY_TRIAGE);
    setReportUrl(null);
    setVitalsResults(null);
    setShowVitalsScan(false);
    vitalsCompletedRef.current = false;
    clearTranscriptBuffers();
    isInterruptedRef.current = false;
    sessionStartedRef.current = false;

    try {
      const connRes = await fetch('/api/connection');
      if (!connRes.ok) throw new Error('Could not reach API server');
      const connData = await connRes.json() as { websocket_url?: string };
      const wsUrl = connData.websocket_url;
      if (!wsUrl) throw new Error('No WebSocket URL provided by server');

      addMsg('system', `🔗 Connecting to server…`);

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onerror = () => {
        addMsg('system', '❌ WebSocket connection failed');
        setStatus('disconnected');
      };

      ws.onopen = () => {
        addMsg('system', '✅ Connected to Nova Sonic');
        ws.send(JSON.stringify({ action: 'start', voiceId: 'tiffany' }));
        addMsg('system', '⚙️ Server configuring session…');
      };

      ws.onmessage = handleWsMessage;

      ws.onclose = (e) => {
        const clean = e.wasClean ? 'Clean' : 'Unclean';
        const codeMap: Record<number, string> = {
          1000: 'Normal closure',
          1001: 'Going away',
          1002: 'Protocol error',
          1003: 'Unsupported data',
          1006: 'Abnormal closure — check network/server',
          1011: 'Internal server error',
          1015: 'TLS handshake failure',
        };
        const reason = codeMap[e.code] ?? `Unknown code: ${e.code}`;
        addMsg('system', `❌ Disconnected (${clean}) — ${reason}`);
        setStatus('disconnected');
        sessionStartedRef.current = false;
        clearTranscriptBuffers();
        stopRecording();
        stopAllAudio();
      };
    } catch (err) {
      addMsg('system', `❌ Failed to connect: ${(err as Error).message}`);
      setStatus('disconnected');
    }
  }, [addMsg, clearTranscriptBuffers, handleWsMessage, stopAllAudio, stopRecording]);
  const disconnect = useCallback(() => {
    stopRecording();
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'stop' }));
      ws.close();
    }
  }, [stopRecording]);
  const sendText = useCallback((text: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !sessionStartedRef.current) return;

    if (pendingMedia) {
      ws.send(JSON.stringify({
        action: 'media',
        text: text || 'Please analyze this image',
        link: pendingMedia.link,
        mediaType: pendingMedia.type,
        fileName: pendingMedia.name,
      }));
      addMsg('user', `🖼️ You: ${text || 'Please analyze this image'} [${pendingMedia.name}]`);
      setPendingMedia(null);
    } else if (text.trim()) {
      ws.send(JSON.stringify({ action: 'text', message: text }));
      addMsg('user', `💬 You: ${text}`);
    }
  }, [addMsg, pendingMedia]);
  const handleMediaUpload = useCallback(async (file: File) => {
    const previewUrl = URL.createObjectURL(file);
    const msgId = addMsg('user', `📤 Uploading ${file.name}…`);
    setTriage(prev => ({ ...prev, images: [...prev.images, previewUrl] }));

    try {
      const s3Uri = await uploadToS3(file);
      updateMsgById(msgId, `🖼️ ${file.name} — ready to send`);
      setPendingMedia({ link: s3Uri, type: file.type, name: file.name });
    } catch (err) {
      updateMsgById(msgId, `❌ Upload failed: ${(err as Error).message}`);
    }
  }, [addMsg, updateMsgById, uploadToS3]);
  const handleVitalsComplete = useCallback(async (result: {
    hr: number | null; rr: number | null;
    hr_conf: number | null; rr_conf: number | null;
  }) => {
    if (vitalsCompletedRef.current) return;
    vitalsCompletedRef.current = true;

    setVitalsResults({ hr: result.hr, rr: result.rr });
    setShowVitalsScan(false);
    addMsg('system', `💓 Scan done — Heart rate: ${result.hr ?? '?'} bpm · Respiration: ${result.rr ?? '?'} rpm`);

    let hrChartUri: string | null = null;
    let rrChartUri: string | null = null;
    try {
      const [hrFile, rrFile] = await Promise.all([
        canvasToFile('vl-hr-chart', 'vitals_hr_chart.png'),
        canvasToFile('vl-rr-chart', 'vitals_rr_chart.png'),
      ]);
      [hrChartUri, rrChartUri] = await Promise.all([uploadToS3(hrFile), uploadToS3(rrFile)]);
    } catch { /* chart upload failed — continue anyway */ }

    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        action: 'vitalsResult',
        hr: result.hr, rr: result.rr,
        hr_conf: result.hr_conf, rr_conf: result.rr_conf,
        hr_chart: hrChartUri, rr_chart: rrChartUri,
      }));
    }
  }, [addMsg, uploadToS3]);
  const isConnected = status !== 'disconnected' && status !== 'connecting';

  return (
    <div className="app-shell">
      <Header
        status={status}
        isConnected={isConnected}
        isConnecting={status === 'connecting'}
        onToggleConnection={isConnected ? disconnect : connect}
      />
      <div className="panels-container">
        <ConversationPanel
          messages={messages}
          messagesEndRef={messagesEndRef}
          isConnected={isConnected}
          pendingMedia={pendingMedia}
          requestMediaPulsing={requestMediaPulsing}
          onSendText={sendText}
          onMediaUpload={handleMediaUpload}
        />
        <PatientDashboard
          triage={triage}
          reportUrl={reportUrl}
          vitalsResults={vitalsResults}
        />
      </div>
      {showVitalsScan && <VitalsModal onComplete={handleVitalsComplete} />}
    </div>
  );
}
