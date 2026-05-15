import { Star, StarHalf } from "lucide-react";

export function StarRow({ rating, showNumber = true }: { rating: number; showNumber?: boolean }) {
  const r = Math.max(0, Math.min(5, rating));
  const full = Math.floor(r);
  const half = r - full >= 0.5 ? 1 : 0;
  const empty = 5 - full - half;

  return (
    <span className="inline-flex items-center gap-0.5" title={`${r.toFixed(1)} / 5`}>
      {Array.from({ length: full }).map((_, i) => (
        <Star key={`f${i}`} className="w-3 h-3 fill-amber-400 text-amber-400" />
      ))}
      {half ? <StarHalf className="w-3 h-3 fill-amber-400 text-amber-400" /> : null}
      {Array.from({ length: empty }).map((_, i) => (
        <Star key={`e${i}`} className="w-3 h-3 text-slate-700" />
      ))}
      {showNumber && (
        <span className="ml-1 text-[10px] text-slate-500 font-mono">{r.toFixed(1)}</span>
      )}
    </span>
  );
}
