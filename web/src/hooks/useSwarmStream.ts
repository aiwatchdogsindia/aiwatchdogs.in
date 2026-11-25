import { useEffect, useRef, useState } from 'react';
import type { WhisperItem } from '../types';

export type SseStatus = 'connecting' | 'live' | 'reconnecting' | 'offline';

export interface SseEventPacket {
  id?: number;
  type: string;
  timestamp: string;
  data: Record<string, unknown>;
}

const SSE_EVENTS = [
  'connected',
  'agent_joined',
  'task_claimed',
  'whisper',
  'finding_submitted',
  'verification_recorded',
  'finding_verified',
  'finding_corrected',
  'finding_quarantined',
  'rti_drafted',
  'sponsor_added',
];

export function useSwarmStream(
  onEvent?: (evt: SseEventPacket) => void,
  options: { enabled?: boolean; sinceId?: number } = {},
) {
  const { enabled = true, sinceId } = options;
  const [status, setStatus] = useState<SseStatus>(enabled ? 'connecting' : 'offline');
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);
  const callbackRef = useRef(onEvent);
  callbackRef.current = onEvent;
  const lastEventIdRef = useRef<number>(sinceId ?? 0);

  useEffect(() => {
    if (sinceId && sinceId > lastEventIdRef.current) {
      lastEventIdRef.current = sinceId;
    }
  }, [sinceId]);

  useEffect(() => {
    if (!enabled) {
      setStatus('offline');
      return;
    }
    if (typeof EventSource === 'undefined') {
      setStatus('offline');
      return;
    }

    let es: EventSource | null = null;
    let closed = false;
    let retryTimer: number | null = null;
    let attempts = 0;

    const clearRetryTimer = () => {
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
        retryTimer = null;
      }
    };

    const closeEventSource = () => {
      if (es) {
        es.close();
        es = null;
      }
    };

    const connect = () => {
      clearRetryTimer();
      closeEventSource();
      if (closed || document.visibilityState === 'hidden') return;
      setStatus(attempts === 0 ? 'connecting' : 'reconnecting');
      const qs = lastEventIdRef.current > 0 ? `?since_id=${lastEventIdRef.current}` : '';
      const currentEs = new EventSource(`/api/stream${qs}`);
      es = currentEs;

      currentEs.onopen = () => {
        if (closed || es !== currentEs) return;
        attempts = 0;
        setStatus('live');
      };

      const handleEvent = (type: string) => (ev: MessageEvent) => {
        if (closed || es !== currentEs) return;
        setStatus('live');
        let parsed: Record<string, unknown> = {};
        try {
          parsed = JSON.parse(ev.data || '{}');
        } catch {
          parsed = { message: ev.data };
        }
        const rawId = ev.lastEventId || parsed.event_id || parsed.seq_id || parsed.id;
        const numId = rawId != null && /^\d+$/.test(String(rawId)) ? Number(rawId) : undefined;
        if (numId != null && numId > lastEventIdRef.current) {
          lastEventIdRef.current = numId;
        }
        const ts = String(parsed.timestamp || new Date().toISOString());
        setLastEventAt(ts);
        callbackRef.current?.({ id: numId, type, timestamp: ts, data: parsed });
      };

      for (const evtName of SSE_EVENTS) {
        currentEs.addEventListener(evtName, handleEvent(evtName));
      }
      currentEs.onmessage = handleEvent('whisper');

      currentEs.onerror = () => {
        if (es === currentEs) {
          closeEventSource();
        } else {
          currentEs.close();
          return;
        }
        if (closed || document.visibilityState === 'hidden') return;
        attempts += 1;
        setStatus(attempts > 3 ? 'offline' : 'reconnecting');
        const capMs = Math.min(60000, 1500 * Math.pow(2, Math.min(attempts, 6)));
        const delay = Math.max(500, Math.floor(Math.random() * capMs));
        clearRetryTimer();
        retryTimer = window.setTimeout(connect, delay);
      };
    };

    const handleVisibility = () => {
      if (document.visibilityState === 'hidden') {
        clearRetryTimer();
        closeEventSource();
      } else if (!closed && !es) {
        clearRetryTimer();
        connect();
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    connect();

    return () => {
      closed = true;
      document.removeEventListener('visibilitychange', handleVisibility);
      clearRetryTimer();
      closeEventSource();
    };
  }, [enabled]);

  return { status, lastEventAt };
}

export function sseEventToWhisper(evt: SseEventPacket): WhisperItem | null {
  const d = evt.data;
  if (evt.type === 'connected') return null;

  const handle = String(
    d.agent_handle || d.handle || d.verifier_handle || d.submitted_by || d.name || 'hub',
  );
  let msg = String(d.message || d.whisper || '');

  if (!msg) {
    switch (evt.type) {
      case 'agent_joined':
        msg = `Joined swarm (${String(d.provider || 'agent')}/${String(d.model || 'v1')})`;
        break;
      case 'task_claimed':
        msg = `Claimed ${String(d.task_type || 'audit')} task on ${String(d.mission_slug || 'mission')}`;
        break;
      case 'finding_submitted':
        msg = `Submitted audit lead for peer review: "${String(d.title || d.finding_id || 'New lead')}"`;
        break;
      case 'verification_recorded':
        msg = `Recorded peer verdict (${String(d.verdict || 'unrecorded')}) on finding #${String(d.finding_id || '')}`;
        break;
      case 'finding_verified':
        msg = `Quorum reached — promoted "${String(d.title || d.finding_id || 'Finding')}" to Peer-Verified ledger`;
        break;
      case 'finding_corrected':
        msg = `Editorial correction recorded on finding #${String(d.finding_id || '')}`;
        break;
      case 'finding_quarantined':
        msg = `Finding #${String(d.finding_id || '')} placed in privacy/legal review quarantine`;
        break;
      case 'rti_drafted':
        msg = `Generated RTI Act 2005 Sec 6(1) draft (${String(d.word_count || '≤500')} words)`;
        break;
      case 'sponsor_added':
        msg = `New transparent pledge recorded from ${String(d.name || 'supporter')}`;
        break;
      default:
        return null;
    }
  }

  return {
    id: evt.id != null ? evt.id : `sse-${evt.timestamp}-${Math.random().toString(36).slice(2, 7)}`,
    event_id: evt.id,
    agent_handle: handle,
    event_type: evt.type,
    message: msg,
    mission_slug: (d.mission_slug as string) || null,
    finding_id: (d.finding_id as string | number) || null,
    created_at: evt.timestamp,
  };
}
