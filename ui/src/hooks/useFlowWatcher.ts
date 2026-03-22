import { useState, useEffect, useRef } from 'react';
import { useWebSocket } from './useWebSocket';
import { api } from '../api/client';
import type { DiscoveredFlow } from '../api/types';

interface UseFlowWatcherReturn {
  flows: DiscoveredFlow[];
  isConnected: boolean;
}

const RELEVANT_EVENTS = [
  'flow.file_changed',
  'flow.file_error',
  'flow.file_valid',
];

export function useFlowWatcher(): UseFlowWatcherReturn {
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;
  const { eventQueue, clearQueue, isConnected } = useWebSocket(wsUrl);
  const [flows, setFlows] = useState<DiscoveredFlow[]>([]);
  const fetchingRef = useRef(false);
  const mountedRef = useRef(true);

  // Track mounted state to avoid setState after unmount
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Initial fetch
  useEffect(() => {
    fetchFlows();
  }, []);

  // Re-fetch when relevant file watcher events arrive
  useEffect(() => {
    if (eventQueue.length === 0) return;

    const hasRelevant = eventQueue.some((event) =>
      RELEVANT_EVENTS.includes(event.type),
    );
    if (hasRelevant) {
      fetchFlows();
    }
    clearQueue();
  }, [eventQueue, clearQueue]);

  async function fetchFlows() {
    // Debounce: skip if a fetch is already in-flight
    if (fetchingRef.current) return;
    fetchingRef.current = true;

    try {
      const result = await api.flows.list();
      if (mountedRef.current) {
        setFlows(result);
      }
    } catch (err) {
      // On error, keep the previous flows list
      console.error('Failed to fetch flows:', err);
    } finally {
      fetchingRef.current = false;
    }
  }

  return { flows, isConnected };
}
