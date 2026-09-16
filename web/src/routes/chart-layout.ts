export const DAILY_CHART_COLUMN_WIDTH = 34;
export const DAILY_CHART_GAP = 20.8;
const DAILY_CHART_HORIZONTAL_PADDING = 8;

export function getFittingChartPointCount(chartWidth: number, pointCount: number): number {
  if (pointCount <= 0 || !Number.isFinite(chartWidth) || chartWidth <= 0) return 0;
  const availableWidth = Math.max(0, chartWidth - DAILY_CHART_HORIZONTAL_PADDING);
  return Math.min(pointCount, Math.floor((availableWidth + DAILY_CHART_GAP) / (DAILY_CHART_COLUMN_WIDTH + DAILY_CHART_GAP)));
}
