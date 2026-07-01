import { memo, useEffect, useState } from 'react';

interface FontSizeControlProps {
  description: string;
  label: string;
  max: number;
  min: number;
  onChange: (value: number) => void;
  value: number;
}

export const FontSizeControl = memo(function FontSizeControl({
  description,
  label,
  max,
  min,
  onChange,
  value,
}: FontSizeControlProps) {
  // 本地 draft 允许用户在输入框中键入中间状态（如"1" → "18"）
  // 只在失焦或按 Enter 时才提交到外层状态
  const [draft, setDraft] = useState(String(value));

  // 外层 value 变化时同步 draft（例如拖动滑块后更新文字框）
  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  const commit = (raw: string) => {
    const n = Number(raw);
    if (!Number.isFinite(n)) {
      // 非法输入 → 重置显示
      setDraft(String(value));
      return;
    }
    const clamped = Math.min(Math.max(Math.round(n), min), max);
    onChange(clamped);
    setDraft(String(clamped));
  };

  return (
    <div className="settings-size-row">
      <span>
        <strong>{label}</strong>
        <small>{description}</small>
      </span>
      <div className="settings-size-control">
        {/* 滑块：直接受控，拖动立即生效 */}
        <input
          aria-label={`${label}滑动调整`}
          max={max}
          min={min}
          step={1}
          type="range"
          value={value}
          onChange={event => {
            const n = Number(event.target.value);
            onChange(Math.min(Math.max(Math.round(n), min), max));
          }}
        />
        {/* 数字输入框：本地 draft，失焦或 Enter 才提交 */}
        <input
          aria-label={`${label}数值`}
          max={max}
          min={min}
          step={1}
          type="number"
          value={draft}
          onChange={event => setDraft(event.target.value)}
          onBlur={event => commit(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter') commit((event.target as HTMLInputElement).value);
          }}
        />
        <em>px</em>
      </div>
    </div>
  );
});
