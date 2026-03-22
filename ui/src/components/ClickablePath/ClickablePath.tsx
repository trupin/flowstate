import { useState } from 'react';
import { api } from '../../api/client';
import './ClickablePath.css';

interface ClickablePathProps {
  path: string;
  truncate?: number;
}

function truncatePath(p: string, maxLen: number): string {
  if (p.length <= maxLen) return p;
  return '...' + p.slice(-(maxLen - 3));
}

export function ClickablePath({ path, truncate = 30 }: ClickablePathProps) {
  const [status, setStatus] = useState<'idle' | 'success' | 'error'>('idle');

  const handleClick = async (e: React.MouseEvent | React.KeyboardEvent) => {
    e.stopPropagation();
    const ide = localStorage.getItem('flowstate-ide') ?? 'code';
    try {
      await api.open(path, ide);
      setStatus('success');
      setTimeout(() => setStatus('idle'), 1500);
    } catch (err) {
      console.error('Failed to open path:', err);
      setStatus('error');
      setTimeout(() => setStatus('idle'), 3000);
    }
  };

  const className = [
    'clickable-path',
    status === 'success' ? 'clickable-path-success' : '',
    status === 'error' ? 'clickable-path-error' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <span
      className={className}
      title={path}
      onClick={(e) => void handleClick(e)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter') void handleClick(e);
      }}
    >
      {status === 'error' && '\u26A0 '}
      {truncatePath(path, truncate)}
    </span>
  );
}
