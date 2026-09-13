/** Display helpers. These format numbers; they never invent them. */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit]}`;
}

export function formatArea(squareMetres: number): string {
  if (squareMetres >= 10_000) {
    return `${(squareMetres / 10_000).toLocaleString(undefined, {
      maximumFractionDigits: 2,
    })} ha`;
  }
  return `${squareMetres.toLocaleString(undefined, { maximumFractionDigits: 1 })} m²`;
}

export function formatCount(value: number): string {
  return value.toLocaleString();
}

export function fileExtension(name: string): string {
  const index = name.lastIndexOf(".");
  return index === -1 ? "" : name.slice(index).toLowerCase();
}
