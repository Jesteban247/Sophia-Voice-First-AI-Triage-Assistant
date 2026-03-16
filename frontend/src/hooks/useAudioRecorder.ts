import { useRef, useState, useCallback } from 'react';

interface UseAudioRecorderOptions {
  onAudioChunk: (base64: string) => void;
}

export function useAudioRecorder({ onAudioChunk }: UseAudioRecorderOptions) {
  const [isRecording, setIsRecording] = useState(false);
  const audioCtxRef = useRef<AudioContext | null>(null);

  const startRecording = useCallback(async (): Promise<void> => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });

      const ctx = new AudioContext();
      audioCtxRef.current = ctx;

      const source = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);

      processor.onaudioprocess = (e: AudioProcessingEvent) => {
        const input = e.inputBuffer.getChannelData(0);

        // Downsample to 16 kHz
        const ratio = ctx.sampleRate / 16000;
        const outLen = Math.floor(input.length / ratio);
        const int16 = new Int16Array(outLen);
        for (let i = 0; i < outLen; i++) {
          const idx = Math.floor(i * ratio);
          int16[i] = Math.max(-32768, Math.min(32767, input[idx] * 32768));
        }

        const bytes = new Uint8Array(int16.buffer);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        onAudioChunk(btoa(binary));
      };

      source.connect(processor);
      processor.connect(ctx.destination);
      setIsRecording(true);
    } catch (err) {
      console.error('Mic access denied:', err);
      throw err;
    }
  }, [onAudioChunk]);

  const stopRecording = useCallback(() => {
    if (audioCtxRef.current) {
      audioCtxRef.current.close();
      audioCtxRef.current = null;
    }
    setIsRecording(false);
  }, []);

  return { isRecording, startRecording, stopRecording };
}
