export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'recording';

export type MessageType = 'user' | 'assistant' | 'system' | 'tool';

export interface ChatMessage {
  id: string;
  type: MessageType;
  content: string;
  timestamp: number;
}

export interface TriageIdentity {
  name?: string | null;
  id_number?: string | null;
  dob?: string | null;
  age?: string | null;
  sex?: string | null;
}

export interface TriageClinical {
  chief_complaint?: string | null;
  location?: string | null;
  duration?: string | null;
  severity?: string | null;
  visual_evidence?: string | null;
  medical_history?: string | null;
  priority?: 'P1' | 'P2' | 'P3' | null;
}

export interface TriageState {
  stage: 'identity' | 'clinical' | 'vitals' | 'done';
  identity: TriageIdentity;
  clinical: TriageClinical;
  images: string[];
  report_url?: string | null;
}

export interface PendingMedia {
  link: string;
  type: string;
  name: string;
}

export interface VitalsResult {
  hr: number | null;
  rr: number | null;
  hr_conf: number | null;
  rr_conf: number | null;
  hr_chart: string | null;
  rr_chart: string | null;
}
