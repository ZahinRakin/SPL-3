/** Small display helpers shared by several components. */

export function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  if (ext === 'pdf') return '📄';
  if (ext === 'docx' || ext === 'doc') return '📝';
  if (ext === 'txt') return '📃';
  if (ext === 'html' || ext === 'htm') return '🌐';
  return '📋';
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * A colour at reduced opacity. Works with CSS variables (`var(--accent)`), which is why
 * it uses color-mix instead of parsing hex. Use it in style bindings or D3 `.style()`,
 * not in SVG presentation attributes (those don't resolve CSS variables).
 */
export function withAlpha(color: string, alpha: number): string {
  return `color-mix(in srgb, ${color} ${Math.round(alpha * 100)}%, transparent)`;
}
