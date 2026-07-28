export function cn(...inputs) {
  return inputs
    .flatMap((i) => (Array.isArray(i) ? i : [i]))
    .filter(Boolean)
    .join(' ');
}
