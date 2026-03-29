import { useState } from 'react';
import type { ReactNode } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useArtifacts } from '../../hooks/useArtifacts';
import styles from './ArtifactDisplay.module.css';

// --- Decision artifact types ---

interface DecisionContent {
  decision: string;
  reasoning: string;
  confidence: number;
}

function isDecisionContent(value: unknown): value is DecisionContent {
  if (value == null || typeof value !== 'object') return false;
  const obj = value as Record<string, unknown>;
  return (
    typeof obj.decision === 'string' &&
    typeof obj.reasoning === 'string' &&
    typeof obj.confidence === 'number'
  );
}

// --- Confidence helpers ---

type ConfidenceLevel = 'high' | 'medium' | 'low';

function getConfidenceLevel(confidence: number): ConfidenceLevel {
  if (confidence > 0.8) return 'high';
  if (confidence > 0.5) return 'medium';
  return 'low';
}

function getConfidenceFillClass(level: ConfidenceLevel): string {
  switch (level) {
    case 'high':
      return styles.confidenceHigh ?? '';
    case 'medium':
      return styles.confidenceMedium ?? '';
    case 'low':
      return styles.confidenceLow ?? '';
  }
}

function getConfidenceValueClass(level: ConfidenceLevel): string {
  switch (level) {
    case 'high':
      return styles.confidenceValueHigh ?? '';
    case 'medium':
      return styles.confidenceValueMedium ?? '';
    case 'low':
      return styles.confidenceValueLow ?? '';
  }
}

// --- Collapsible section ---

interface ArtifactSectionProps {
  title: string;
  children: ReactNode;
  defaultExpanded?: boolean;
}

function ArtifactSection({
  title,
  children,
  defaultExpanded = true,
}: ArtifactSectionProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const handleToggle = () => {
    setExpanded((prev) => !prev);
  };

  return (
    <div className={styles.artifactSection}>
      <div
        className={styles.sectionHeader}
        onClick={handleToggle}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleToggle();
          }
        }}
        aria-expanded={expanded}
      >
        <span className={styles.sectionChevron}>
          {expanded ? '\u25BE' : '\u25B8'}
        </span>
        <span className={styles.sectionTitle}>{title}</span>
      </div>
      {expanded && <div className={styles.sectionBody}>{children}</div>}
    </div>
  );
}

// --- Decision display ---

interface DecisionDisplayProps {
  content: string;
}

function DecisionDisplay({ content }: DecisionDisplayProps) {
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    // Malformed JSON: show raw content as code block
    return <pre className={styles.rawContent}>{content}</pre>;
  }

  if (!isDecisionContent(parsed)) {
    // Valid JSON but wrong shape: show raw content
    return (
      <pre className={styles.rawContent}>{JSON.stringify(parsed, null, 2)}</pre>
    );
  }

  const level = getConfidenceLevel(parsed.confidence);
  const percentage = Math.round(parsed.confidence * 100);

  return (
    <div>
      <div className={styles.decisionRow}>
        <span className={styles.decisionLabel}>Target</span>
        <span className={styles.targetBadge}>{parsed.decision}</span>
      </div>
      <div className={styles.decisionRow}>
        <span className={styles.decisionLabel}>Confidence</span>
        <div className={styles.confidenceContainer}>
          <div className={styles.confidenceBar}>
            <div
              className={`${styles.confidenceFill} ${getConfidenceFillClass(level)}`}
              style={{ width: `${String(percentage)}%` }}
            />
          </div>
          <span
            className={`${styles.confidenceValue} ${getConfidenceValueClass(level)}`}
          >
            {percentage}%
          </span>
        </div>
      </div>
      {parsed.reasoning && (
        <div className={styles.decisionRow}>
          <span className={styles.decisionLabel}>Reasoning</span>
        </div>
      )}
      {parsed.reasoning && (
        <div className={styles.reasoningText}>{parsed.reasoning}</div>
      )}
    </div>
  );
}

// --- Summary display ---

const REMARK_PLUGINS = [remarkGfm];
const SUMMARY_TRUNCATE_LENGTH = 500;

interface SummaryDisplayProps {
  content: string;
}

function SummaryDisplay({ content }: SummaryDisplayProps) {
  const [expanded, setExpanded] = useState(false);
  const needsTruncation = content.length > SUMMARY_TRUNCATE_LENGTH;
  const displayContent =
    needsTruncation && !expanded
      ? content.slice(0, SUMMARY_TRUNCATE_LENGTH) + '...'
      : content;

  return (
    <div>
      <div className={styles.summaryContent}>
        <Markdown remarkPlugins={REMARK_PLUGINS}>{displayContent}</Markdown>
      </div>
      {needsTruncation && (
        <button
          className={styles.expandButton}
          onClick={() => setExpanded((prev) => !prev)}
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  );
}

// --- Main component ---

interface ArtifactDisplayProps {
  runId: string;
  taskId: string;
  taskStatus: string;
}

export function ArtifactDisplay({
  runId,
  taskId,
  taskStatus,
}: ArtifactDisplayProps) {
  const { decision, summary, loading } = useArtifacts(
    runId,
    taskId,
    taskStatus,
  );

  if (loading) {
    return <div className={styles.artifactLoading}>Loading artifacts...</div>;
  }

  if (!decision && !summary) {
    return null;
  }

  return (
    <>
      {decision && (
        <ArtifactSection title="Decision">
          <DecisionDisplay content={decision.content} />
        </ArtifactSection>
      )}
      {summary && (
        <ArtifactSection title="Summary">
          <SummaryDisplay content={summary.content} />
        </ArtifactSection>
      )}
    </>
  );
}
