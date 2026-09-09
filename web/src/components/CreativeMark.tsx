/** Decorative topic identifiers, not generated content previews. */
export function CreativeMark({ variant = 3 }: { variant?: number }) {
  return <svg className={`creative-mark mark-${variant}`} viewBox="0 0 100 100" aria-hidden="true">
    {variant === 0 ? <><circle cx="44" cy="44" r="29" fill="#1675f8" /><circle cx="66" cy="65" r="22" fill="#a9ceff" opacity=".9" /></>
      : variant === 1 ? <><path d="M20 23h62v23H20z" fill="#ff8b45" /><path d="M20 51h48v20H20z" fill="#ffb084" /><path d="M20 76h36v10H20z" fill="#ffd8c1" /></>
      : variant === 2 ? <><path d="M16 15a70 70 0 0 1 70 70H16z" fill="#48aa90" /><path d="M36 85a50 50 0 0 1 50-50v50z" fill="#9bdac7" /></>
      : <><circle cx="36" cy="35" r="23" fill="#2788fb" /><path d="M72 30 91 63H53z" fill="#ff86b1" /><rect x="14" y="63" width="43" height="23" rx="5" fill="#ffc45b" /></>}
  </svg>;
}
