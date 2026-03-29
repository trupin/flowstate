import { useState, useEffect } from 'react';
import { ApiError } from '../api/client';

interface ArtifactContent {
  name: string;
  content: string;
  contentType: string;
}

interface UseArtifactsReturn {
  decision: ArtifactContent | null;
  summary: ArtifactContent | null;
  loading: boolean;
}

/**
 * Fetch decision and summary artifacts for a completed/failed task.
 * Only fetches when both runId and taskId are provided and the task
 * status indicates execution has finished. Returns null for artifacts
 * that don't exist (404 is treated as "no artifact", not an error).
 */
export function useArtifacts(
  runId: string | undefined,
  taskId: string | undefined,
  taskStatus: string | undefined,
): UseArtifactsReturn {
  const [decision, setDecision] = useState<ArtifactContent | null>(null);
  const [summary, setSummary] = useState<ArtifactContent | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // Only fetch for completed or failed tasks
    if (
      !runId ||
      !taskId ||
      (taskStatus !== 'completed' && taskStatus !== 'failed')
    ) {
      setDecision(null);
      setSummary(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);

    async function fetchArtifact(
      name: string,
    ): Promise<ArtifactContent | null> {
      try {
        const response = await fetch(
          `/api/runs/${runId}/tasks/${taskId}/artifacts/${name}`,
        );
        if (!response.ok) {
          if (response.status === 404) {
            return null;
          }
          throw new ApiError(response.status, response.statusText);
        }
        const content = await response.text();
        const contentType =
          response.headers.get('content-type') ?? 'text/plain';
        return { name, content, contentType };
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          return null;
        }
        // Silently swallow other errors (network issues, etc.)
        return null;
      }
    }

    Promise.all([fetchArtifact('decision'), fetchArtifact('summary')])
      .then(([decisionResult, summaryResult]) => {
        if (cancelled) return;
        setDecision(decisionResult);
        setSummary(summaryResult);
      })
      .catch(() => {
        // Should not happen since individual fetches catch errors
        if (!cancelled) {
          setDecision(null);
          setSummary(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [runId, taskId, taskStatus]);

  return { decision, summary, loading };
}
