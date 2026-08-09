/**
 * The rail's icons, drawn rather than fetched.
 *
 * Three small SVGs inline in the bundle instead of an icon package: the whole set is under
 * a kilobyte of markup, and a dependency that ships a thousand glyphs to draw three is one
 * that has to be audited, updated and licensed for the rest of the project's life. They are
 * in a file of their own because `App.tsx` is where features land, and eighty lines of path
 * data at the bottom of it is eighty lines somebody scrolls past to reach the application.
 *
 * Each is `aria-hidden`: the button around it carries the name, so an icon that also had
 * one would be read twice.
 */

export function ChartIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <rect x="2" y="9" width="3.4" height="7" fill="currentColor" />
      <rect x="7.3" y="5" width="3.4" height="11" fill="currentColor" />
      <rect x="12.6" y="2" width="3.4" height="14" fill="currentColor" />
    </svg>
  );
}

export function DashboardIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <rect x="2" y="2" width="6" height="7" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <rect x="10" y="2" width="6" height="4" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <rect x="2" y="11" width="6" height="5" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <rect x="10" y="8" width="6" height="8" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

export function DataIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <rect x="2" y="3" width="14" height="12" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <line x1="2" y1="7" x2="16" y2="7" stroke="currentColor" strokeWidth="1.4" />
      <line x1="7" y1="7" x2="7" y2="15" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}
