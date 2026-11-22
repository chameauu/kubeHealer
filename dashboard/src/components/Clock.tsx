import { useNow } from '../hooks/useNow';
import { formatClock } from '../lib/format';

/** The clock ticks in its own component so the active page is not re-rendered. */
export function Clock() {
  const now = useNow(1000);
  return (
    <span className="clock mono" aria-label="current UTC time">
      {formatClock(now)}
    </span>
  );
}
