import { useEffect, useRef, useState } from 'react';

interface VitalsResult {
  hr: number | null;
  rr: number | null;
  hr_conf: number | null;
  rr_conf: number | null;
}

interface VitalsScanWidgetProps {
  onComplete: (result: VitalsResult) => void;
}

const DURATION_MS = 30000;

export function VitalsScanWidget({ onComplete }: VitalsScanWidgetProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const statusRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<HTMLDivElement>(null);
  const latestRef = useRef<VitalsResult>({ hr: null, rr: null, hr_conf: null, rr_conf: null });
  const hasCompleted = useRef(false);
  const startRef = useRef<(() => void) | null>(null);
  const [needsClick, setNeedsClick] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let vl: any = null;
    let stopped = false;
    const hrSeries: Array<{ t: number; v: number }> = [];
    const rrSeries: Array<{ t: number; v: number }> = [];

    const drawSeries = (canvasId: string, series: Array<{ t: number; v: number }>, color: string, unit: string) => {
      const canvas = document.getElementById(canvasId) as HTMLCanvasElement | null;
      if (!canvas || series.length < 2) return;
      const width = 600;
      const height = 200;
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = '#0b1224';
      ctx.fillRect(0, 0, width, height);
      ctx.strokeStyle = 'rgba(148,163,184,0.2)';
      ctx.lineWidth = 1;
      for (let i = 1; i < 6; i += 1) {
        const y = (i / 6) * height;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
      }
      for (let i = 1; i < 6; i += 1) {
        const x = (i / 6) * width;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      }
      const values = series.map(s => s.v);
      const min = Math.min(...values);
      const max = Math.max(...values);
      const range = Math.max(1, max - min);
      ctx.fillStyle = 'rgba(148,163,184,0.7)';
      ctx.font = '12px "Space Grotesk", system-ui, sans-serif';
      ctx.fillText(`${Math.round(max)} ${unit}`, 10, 16);
      ctx.fillText(`${Math.round(min)} ${unit}`, 10, height - 8);
      ctx.save();
      ctx.translate(8, height / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.fillText(unit, 0, 0);
      ctx.restore();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      series.forEach((point, idx) => {
        const x = (idx / (series.length - 1)) * (width - 20) + 10;
        const y = height - 10 - ((point.v - min) / range) * (height - 20);
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    };

    const handleVitals = (result: any) => {
      const vitals = result?.vitals ?? result?.vital_signs ?? result;
      const hr = vitals?.heart_rate ?? vitals?.hr;
      const rr = vitals?.respiratory_rate ?? vitals?.rr;
      const now = Date.now();

      if (hr?.value != null) {
        const hrValue = Math.round(hr.value);
        latestRef.current.hr = hrValue;
        latestRef.current.hr_conf = hr.confidence != null ? Math.round(hr.confidence * 100) : null;
        hrSeries.push({ t: now, v: hrValue });
        if (hrSeries.length > 120) hrSeries.shift();
        drawSeries('vl-hr-chart', hrSeries, '#22c55e', 'bpm');
      }

      if (rr?.value != null) {
        const rrValue = Math.round(rr.value);
        latestRef.current.rr = rrValue;
        latestRef.current.rr_conf = rr.confidence != null ? Math.round(rr.confidence * 100) : null;
        rrSeries.push({ t: now, v: rrValue });
        if (rrSeries.length > 120) rrSeries.shift();
        drawSeries('vl-rr-chart', rrSeries, '#38bdf8', 'rpm');
      }
    };

    const start = async () => {
      const proxyUrlEnv = import.meta.env.VITE_VITALLENS_PROXY_URL as string | undefined;
      const fallbackProxy = `${window.location.origin}/api/vitallens`;
      let proxyUrl = (proxyUrlEnv && proxyUrlEnv.trim()) || fallbackProxy;
      if (proxyUrl.startsWith('/')) {
        proxyUrl = `${window.location.origin}${proxyUrl}`;
      }
      if (statusRef.current) statusRef.current.textContent = 'Requesting camera…';
      setErrorMsg(null);

      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
        }

        const mod = await import(/* @vite-ignore */ 'https://cdn.jsdelivr.net/npm/vitallens');
        const VitalLens = mod.VitalLens;
        vl = new VitalLens({ method: 'vitallens', proxyUrl });
        await vl.setVideoStream(stream, videoRef.current);
        vl.addEventListener('vitals', handleVitals);
        vl.startVideoStream();

        setNeedsClick(false);
        if (statusRef.current) statusRef.current.textContent = 'Scanning — stay still and face the camera';
      } catch (err: any) {
        setNeedsClick(true);
        const message = err?.message || 'Camera permission required';
        setErrorMsg(message);
        if (statusRef.current) statusRef.current.textContent = 'Camera permission needed — click Enable Camera';
      }
    };
    startRef.current = () => { void start(); };

    const startAt = Date.now();
    const tick = () => {
      const remaining = Math.max(0, DURATION_MS - (Date.now() - startAt));
      if (timerRef.current) timerRef.current.textContent = `${Math.ceil(remaining / 1000)}s`;
      if (remaining > 0) {
        requestAnimationFrame(tick);
      }
    };
    requestAnimationFrame(tick);
    void start();

    const timeout = setTimeout(() => {
      if (hasCompleted.current) return;
      hasCompleted.current = true;
      stopped = true;
      if (vl) {
        try { vl.stopVideoStream(); } catch {}
      }
      if (stream) {
        stream.getTracks().forEach(track => track.stop());
      }
      drawSeries('vl-hr-chart', hrSeries, '#22c55e', 'bpm');
      drawSeries('vl-rr-chart', rrSeries, '#38bdf8', 'rpm');
      onComplete({ ...latestRef.current });
    }, DURATION_MS);

    if (statusRef.current) {
      statusRef.current.textContent = 'Starting camera — stay still and face the camera';
    }

    return () => {
      clearTimeout(timeout);
      if (!stopped && vl) {
        try { vl.stopVideoStream(); } catch {}
      }
      if (stream) {
        stream.getTracks().forEach(track => track.stop());
      }
      if (vl) {
        try { vl.close(); } catch {}
      }
    };
  }, [onComplete]);

  return (
    <div className="vitals-scan-panel">
      {needsClick && (
        <button className="vitals-enable-btn" onClick={() => startRef.current?.()}>
          Enable Camera
        </button>
      )}
      {errorMsg && <div className="vitals-error">{errorMsg}</div>}
      <div className="vitals-monitor-wrap">
        <video ref={videoRef} className="vitals-video" autoPlay muted playsInline />
      </div>
      <div className="vitals-charts">
        <div className="vitals-chart-card">
          <div className="vitals-chart-title">Heart Rate Curve</div>
          <canvas id="vl-hr-chart" className="vitals-chart" />
          <div className="vitals-chart-axis">
            <span>0s</span>
            <span>30s</span>
          </div>
        </div>
        <div className="vitals-chart-card">
          <div className="vitals-chart-title">Respiratory Rate Curve</div>
          <canvas id="vl-rr-chart" className="vitals-chart" />
          <div className="vitals-chart-axis">
            <span>0s</span>
            <span>30s</span>
          </div>
        </div>
      </div>
      <div className="vitals-monitor-footer">
        <div ref={timerRef} className="vitals-timer">30s</div>
        <div ref={statusRef} className="vitals-status">Initializing…</div>
      </div>
    </div>
  );
}
