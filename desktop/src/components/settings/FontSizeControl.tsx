import { memo } from 'react';

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
  const setValue = (nextValue: number) => {
    if (!Number.isFinite(nextValue)) return;
    onChange(Math.min(Math.max(Math.round(nextValue), min), max));
  };

  return (
    <div className="settings-size-row">
      <span>
        <strong>{label}</strong>
        <small>{description}</small>
      </span>
      <div className="settings-size-control">
        <input
          aria-label={`${label}滑动调整`}
          max={max}
          min={min}
          type="range"
          value={value}
          onChange={event => setValue(Number(event.target.value))}
        />
        <input
          aria-label={`${label}数值`}
          max={max}
          min={min}
          type="number"
          value={value}
          onChange={event => setValue(Number(event.target.value))}
        />
        <em>px</em>
      </div>
    </div>
  );
});
