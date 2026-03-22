import { useRef, useState, useEffect, useCallback } from 'react';
import type { FlowEvent } from '../api/types';

interface UseWebSocketReturn {
  send: (data: unknown) => void;
  subscribe: (flowRunId: string, lastEventTimestamp?: string) => void;
  unsubscribe: (flowRunId: string) => void;
  eventQueue: FlowEvent[];
  clearQueue: () => void;
  isConnected: boolean;
}

export function useWebSocket(url: string): UseWebSocketReturn {
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [eventQueue, setEventQueue] = useState<FlowEvent[]>([]);
  const lastTimestampRef = useRef<string | null>(null);
  const retryDelayRef = useRef(1000);
  const mountedRef = useRef(true);
  const subscribedRunsRef = useRef<Set<string>>(new Set());

  const connect = useCallback(() => {
    if (!mountedRef.current) return;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      retryDelayRef.current = 1000; // reset backoff

      // Re-subscribe to all previously subscribed runs
      subscribedRunsRef.current.forEach((runId) => {
        ws.send(
          JSON.stringify({
            action: 'subscribe',
            flow_run_id: runId,
            payload: {
              flow_run_id: runId,
              last_event_timestamp: lastTimestampRef.current ?? undefined,
            },
          }),
        );
      });
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(String(event.data)) as FlowEvent;
        lastTimestampRef.current = data.timestamp;
        setEventQueue((prev) => [...prev, data]);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      if (!mountedRef.current) return;

      // Reconnect with exponential backoff
      const delay = retryDelayRef.current;
      retryDelayRef.current = Math.min(delay * 2, 30000);
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close(); // triggers onclose -> reconnect
    };
  }, [url]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  const subscribe = useCallback(
    (flowRunId: string, lastEventTimestamp?: string) => {
      subscribedRunsRef.current.add(flowRunId);
      send({
        action: 'subscribe',
        flow_run_id: flowRunId,
        payload: {
          flow_run_id: flowRunId,
          last_event_timestamp: lastEventTimestamp,
        },
      });
    },
    [send],
  );

  const clearQueue = useCallback(() => setEventQueue([]), []);

  const unsubscribe = useCallback(
    (flowRunId: string) => {
      subscribedRunsRef.current.delete(flowRunId);
      send({
        action: 'unsubscribe',
        flow_run_id: flowRunId,
        payload: { flow_run_id: flowRunId },
      });
    },
    [send],
  );

  return { send, subscribe, unsubscribe, eventQueue, clearQueue, isConnected };
}
