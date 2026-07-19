export type FunnelStage = { label: string; value: number; color: string };

/** Plain SVG funnel: each stage is a trapezoid whose width tapers with the value, classic
 * conversion-funnel look without pulling in a charting library. */
export default function FunnelChart({
  stages,
  width = 640,
  segmentHeight = 60,
  gap = 6,
  minWidthRatio = 0.22,
}: {
  stages: FunnelStage[];
  width?: number;
  segmentHeight?: number;
  gap?: number;
  minWidthRatio?: number;
}) {
  const maxValue = Math.max(1, ...stages.map((s) => s.value));
  const widths = stages.map((s) => width * (minWidthRatio + (1 - minWidthRatio) * (s.value / maxValue)));
  const height = stages.length * segmentHeight + (stages.length - 1) * gap;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Application funnel">
      {stages.map((stage, i) => {
        const topWidth = i === 0 ? widths[0] : widths[i - 1];
        const bottomWidth = widths[i];
        const y0 = i * (segmentHeight + gap);
        const y1 = y0 + segmentHeight;
        const points = [
          [(width - topWidth) / 2, y0],
          [(width + topWidth) / 2, y0],
          [(width + bottomWidth) / 2, y1],
          [(width - bottomWidth) / 2, y1],
        ]
          .map((p) => p.join(","))
          .join(" ");
        return (
          <g key={stage.label}>
            <polygon points={points} fill={stage.color} />
            <text x={width / 2} y={(y0 + y1) / 2 - 3} textAnchor="middle" className="fill-white text-sm font-semibold">
              {stage.label}
            </text>
            <text x={width / 2} y={(y0 + y1) / 2 + 15} textAnchor="middle" className="fill-white/85 text-xs">
              {stage.value}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
