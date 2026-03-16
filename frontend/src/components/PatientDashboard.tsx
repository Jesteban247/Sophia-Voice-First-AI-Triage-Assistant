import { TriageState } from '../types';
import { useState, useEffect } from 'react';

interface PatientDashboardProps {
  triage: TriageState;
  reportUrl: string | null;
  vitalsResults: { hr: number | null; rr: number | null } | null;
}

// Convert S3 URI to presigned HTTPS URL
async function s3UriToPresignedUrl(uri: string): Promise<string> {
  if (uri.startsWith('https://')) return uri;
  if (!uri.startsWith('s3://')) return uri;
  
  try {
    const res = await fetch('/api/s3-view-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ s3Uri: uri }),
    });
    
    if (!res.ok) {
      console.error('Failed to get presigned URL:', res.statusText);
      return uri;
    }
    
    const data = await res.json() as { viewUrl?: string };
    return data.viewUrl || uri;
  } catch (err) {
    console.error('Error getting presigned URL:', err);
    return uri;
  }
}

function Field({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="dash-row">
      <span className="dash-label">{label}</span>
      <span className={`dash-value${!value ? ' empty' : ''}`}>{value || '—'}</span>
    </div>
  );
}

const STAGE_LABELS: Record<string, string> = {
  identity: 'IDENTITY',
  clinical: 'CLINICAL',
  vitals:   'VITALS',
  done:     'COMPLETE',
};

const PRIORITY_LABELS: Record<string, string> = {
  P1: 'P1 — EMERGENCY',
  P2: 'P2 — URGENT',
  P3: 'P3 — ROUTINE',
};

export function PatientDashboard({
  triage,
  reportUrl,
  vitalsResults,
}: PatientDashboardProps) {
  const { identity, clinical, images } = triage;
  const stage = triage.stage || 'identity';
  const [tab, setTab] = useState<'case' | 'pdf'>('case');
  
  // State for presigned image URLs
  const [imageUrls, setImageUrls] = useState<string[]>([]);
  
  // Convert S3 URIs to presigned URLs when images change
  useEffect(() => {
    const loadImages = async () => {
      const urls = await Promise.all(
        images.map(uri => s3UriToPresignedUrl(uri))
      );
      setImageUrls(urls);
    };
    
    if (images.length > 0) {
      loadImages();
    } else {
      setImageUrls([]);
    }
  }, [images]);

  return (
    <div className="panel dashboard-panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">Patient Case</div>
          <div className="panel-subtitle">Structured intake and clinical summary</div>
        </div>
        <div className="tab-group">
          <button
            className={`tab-btn ${tab === 'case' ? 'active' : ''}`}
            onClick={() => setTab('case')}
          >
            Case
          </button>
          <button
            className={`tab-btn ${tab === 'pdf' ? 'active' : ''}`}
            onClick={() => setTab('pdf')}
          >
            PDF
          </button>
        </div>
      </div>
      <div className="dashboard-scroll">

        {tab === 'case' && (
          <>

        <div className="dash-section">
          <div className="dash-section-title">👤 Patient Identity</div>
          <div className="dash-row">
            <span className="dash-label">Stage</span>
            <span className="dash-value">
              <span className={`stage-badge ${stage}`}>{STAGE_LABELS[stage] ?? stage.toUpperCase()}</span>
            </span>
          </div>
          <Field label="Name"          value={identity.name} />
          <Field label="ID Number"      value={identity.id_number} />
          <Field label="Date of Birth"  value={identity.dob} />
          <Field label="Age"            value={identity.age ? `${identity.age} yrs` : null} />
          <Field label="Sex"            value={identity.sex} />
        </div>

        <div className="dash-section">
          <div className="dash-section-title">🩺 Clinical Assessment</div>
          <div className="dash-row">
            <span className="dash-label">Priority</span>
            <span className="dash-value">
              {clinical.priority
                ? <span className={`priority-badge ${clinical.priority}`}>{PRIORITY_LABELS[clinical.priority] ?? clinical.priority}</span>
                : <span className="empty">—</span>
              }
            </span>
          </div>
          <Field label="Chief Complaint"  value={clinical.chief_complaint} />
          <Field label="Location"          value={clinical.location} />
          <Field label="Duration"          value={clinical.duration} />
          <Field label="Severity"          value={clinical.severity ? `${clinical.severity}/10` : null} />
          <Field label="Visual Evidence"   value={clinical.visual_evidence} />
          <Field label="Medical History"   value={clinical.medical_history} />
        </div>

        <div className="dash-section">
          <div className="dash-section-title">📸 Clinical Images</div>
          <div className="images-grid">
            {imageUrls.length === 0
              ? <span className="img-thumb-empty">No images uploaded</span>
              : imageUrls.map((url, i) => (
                  <img
                    key={i}
                    className="img-thumb"
                    src={url}
                    title={`Clinical image ${i + 1}`}
                    onClick={() => window.open(url, '_blank')}
                    alt={`Clinical image ${i + 1}`}
                  />
                ))
            }
          </div>
        </div>

        <div className="dash-section">
          <div className="dash-section-title">💓 Vital Signs</div>

          {vitalsResults ? (
            <>
              <div className="dash-row">
                <span className="dash-label">Heart Rate</span>
                <span className="dash-value">{vitalsResults.hr ? `${vitalsResults.hr} bpm` : '—'}</span>
              </div>
              <div className="dash-row">
                <span className="dash-label">Respiration</span>
                <span className="dash-value">{vitalsResults.rr ? `${vitalsResults.rr} rpm` : '—'}</span>
              </div>
            </>
          ) : (
            <div className="dash-row">
              <span className="dash-value empty">Awaiting vital signs scan</span>
            </div>
          )}
        </div>
          </>
        )}

        {tab === 'pdf' && (
          <div className="dash-section">
            <div className="dash-section-title">📄 Medical Report</div>
            {reportUrl ? (
              <>
                <a className="pdf-link" href={reportUrl} target="_blank" rel="noopener noreferrer">
                  Open PDF Report
                </a>
                <div className="pdf-embed-wrap">
                  <iframe src={reportUrl} title="Medical Report" />
                </div>
              </>
            ) : (
              <div className="dash-row">
                <span className="dash-value empty">Report not generated yet</span>
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}
