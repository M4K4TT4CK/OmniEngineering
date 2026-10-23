export type JobStatus = 'queued' | 'running' | 'completed' | 'failed';

export interface Job {
  id: string;
  type: string;
  payload: unknown;
  status: JobStatus;
  /** Number of times execution has started (1 = first try). */
  attempts: number;
  result?: unknown;
  error?: string;
  createdAt: string;
  updatedAt: string;
}

export interface ResizeImagePayload {
  path: string;
  width?: number;
}

export interface SendEmailPayload {
  to: string;
  subject: string;
  body: string;
}

export const JOB_TYPES = ['resize-image', 'send-email'] as const;

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

/** Returns an error message if the job type/payload is invalid, otherwise null. */
export function validateJob(type: unknown, payload: unknown): string | null {
  if (typeof type !== 'string' || !(JOB_TYPES as readonly string[]).includes(type)) {
    return `type must be one of: ${JOB_TYPES.join(', ')}`;
  }
  if (!isObject(payload)) return 'payload must be a JSON object';

  if (type === 'resize-image') {
    if (typeof payload.path !== 'string' || payload.path === '') {
      return 'payload.path must be a non-empty string';
    }
    const w = payload.width;
    if (w !== undefined && (!Number.isInteger(w) || (w as number) < 1 || (w as number) > 10000)) {
      return 'payload.width must be an integer between 1 and 10000';
    }
  } else {
    if (typeof payload.to !== 'string' || !/^[^\s@]+@[^\s@]+$/.test(payload.to)) {
      return 'payload.to must be an email address';
    }
    if (typeof payload.subject !== 'string') return 'payload.subject must be a string';
    if (typeof payload.body !== 'string') return 'payload.body must be a string';
  }
  return null;
}
