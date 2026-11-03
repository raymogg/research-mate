import { useState } from "react";

interface Props {
  value: number | null;
  onChange: (value: number | null) => void;
  size?: "sm" | "md";
}

/** 1-5 stars. Clicking the current value clears it. */
export function RatingStars({ value, onChange, size = "sm" }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const shown = hover ?? value ?? 0;
  return (
    <span className={`stars stars-${size}`} role="radiogroup" aria-label="Rating" onMouseLeave={() => setHover(null)}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          role="radio"
          aria-checked={value === n}
          aria-label={`${n} star${n > 1 ? "s" : ""}`}
          title={value === n ? "Clear rating" : `Rate ${n}`}
          className={n <= shown ? "star on" : "star"}
          onMouseEnter={() => setHover(n)}
          onClick={() => onChange(value === n ? null : n)}
        >
          ★
        </button>
      ))}
    </span>
  );
}
