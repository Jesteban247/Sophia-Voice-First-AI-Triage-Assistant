import { useEffect } from 'react';
import { VitalsScanWidget } from './VitalsScanWidget';

interface VitalsModalProps {
  onComplete: (result: {
    hr: number | null; rr: number | null;
    hr_conf: number | null; rr_conf: number | null;
  }) => void;
}

export function VitalsModal({ onComplete }: VitalsModalProps) {
  // Prevent body scroll while modal is open
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  return (
    <div className="modal-overlay">
      <div className="modal-card modal-card-large">
        <div className="modal-header">
          <span>💓 Vital Signs Scan</span>
          <span className="modal-subtitle">30-second reading — stay still and face the camera</span>
        </div>
        <div className="modal-body">
          <div className="vitals-instructions">
            <div>Center your face in the frame.</div>
            <div>Hold still and keep lighting steady.</div>
            <div>Scan runs for 30 seconds and auto-submits.</div>
          </div>
          <VitalsScanWidget onComplete={onComplete} />
        </div>
      </div>
    </div>
  );
}
