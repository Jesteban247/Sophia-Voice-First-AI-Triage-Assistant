import { useRef, useCallback } from 'react';

export function useAudioPlayback() {
  const ctxRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef(0);
  const activeSourcesRef = useRef<AudioBufferSourceNode[]>([]);

  const stopAllAudio = useCallback(() => {
    const sources = [...activeSourcesRef.current];
    sources.forEach(src => {
      try { src.stop(0); src.disconnect(); } catch { /* already ended */ }
    });
    activeSourcesRef.current = [];

    if (ctxRef.current) {
      try { ctxRef.current.close(); } catch { /* ignore */ }
      ctxRef.current = null;
      nextPlayTimeRef.current = 0;
    }
  }, []);

  const playAudio = useCallback(async (base64Audio: string, sampleRate = 24000) => {
    if (!ctxRef.current) {
      ctxRef.current = new AudioContext({ sampleRate });
      nextPlayTimeRef.current = ctxRef.current.currentTime;
    }

    const ctx = ctxRef.current;

    if (ctx.state === 'suspended') {
      await ctx.resume();
    }

    // Decode base64 → Int16 PCM → Float32
    const binary = atob(base64Audio);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const int16 = new Int16Array(bytes.buffer);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768.0;

    const buffer = ctx.createBuffer(1, float32.length, sampleRate);
    buffer.getChannelData(0).set(float32);

    const now = ctx.currentTime;
    if (nextPlayTimeRef.current < now) nextPlayTimeRef.current = now;

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);

    activeSourcesRef.current.push(source);
    source.onended = () => {
      activeSourcesRef.current = activeSourcesRef.current.filter(s => s !== source);
    };

    source.start(nextPlayTimeRef.current);
    nextPlayTimeRef.current += buffer.duration;
  }, []);

  return { playAudio, stopAllAudio };
}
