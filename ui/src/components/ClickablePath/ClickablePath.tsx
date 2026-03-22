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
  const handleClick = async (e: React.MouseEvent | React.KeyboardEvent) => {
    e.stopPropagation();
    const ide = localStorage.getItem('flowstate-ide') ?? 'code';
    try {
      await api.open(path, ide);
    } catch (err) {
      console.error('Failed to open path:', err);
    }
  };

  return (
    <span
      className="clickable-path"
      title={path}
      onClick={(e) => void handleClick(e)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter') void handleClick(e);
      }}
    >
      {truncatePath(path, truncate)}
    </span>
  );
}
