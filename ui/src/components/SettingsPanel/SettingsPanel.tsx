import { useState } from 'react';
import './SettingsPanel.css';

const IDE_PRESETS = [
  { name: 'VS Code', command: 'code' },
  { name: 'Cursor', command: 'cursor' },
  { name: 'Zed', command: 'zed' },
  { name: 'Sublime Text', command: 'subl' },
  { name: 'Terminal (open)', command: 'open' },
];

interface SettingsPanelProps {
  onClose: () => void;
}

export function SettingsPanel({ onClose }: SettingsPanelProps) {
  const [ide, setIde] = useState(
    localStorage.getItem('flowstate-ide') ?? 'code',
  );
  const [custom, setCustom] = useState('');
  const isCustom = !IDE_PRESETS.some((p) => p.command === ide);

  const handleSelect = (command: string) => {
    setIde(command);
    localStorage.setItem('flowstate-ide', command);
  };

  const handleCustom = () => {
    if (custom.trim()) {
      handleSelect(custom.trim());
    }
  };

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div
        className="settings-panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Settings"
      >
        <div className="settings-header">
          <h3>Settings</h3>
          <button className="settings-close-btn" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="settings-section">
          <label className="settings-label">Open paths with:</label>
          <div className="ide-presets">
            {IDE_PRESETS.map((p) => (
              <button
                key={p.command}
                className={`ide-btn ${ide === p.command ? 'active' : ''}`}
                onClick={() => handleSelect(p.command)}
              >
                {p.name}
              </button>
            ))}
          </div>
          <div className="ide-custom">
            <input
              type="text"
              placeholder="Custom command..."
              value={isCustom ? ide : custom}
              onChange={(e) => setCustom(e.target.value)}
            />
            <button className="ide-custom-btn" onClick={handleCustom}>
              Set
            </button>
          </div>
          {isCustom && (
            <div className="ide-current">
              Current: <code>{ide}</code>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
