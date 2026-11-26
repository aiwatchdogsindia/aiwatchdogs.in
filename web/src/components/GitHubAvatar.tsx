import { useEffect, useMemo, useRef, useState } from 'react';
import '../styles/live-swarm-viewer.css';

/**
 * Valid GitHub username syntax:
 * 1–39 ASCII alphanumeric chars or single hyphens, cannot begin/end with hyphen.
 */
const GITHUB_HANDLE_RE = /^[a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38}$/;

/** Internal/synthetic placeholder handles that should never hit github.com. */
const SYNTHETIC_HANDLES = new Set([
  'hub',
  'system',
  'swarm',
  'swarm-node',
  'unknown-node',
  'peer-node',
  'coordinator',
  'anonymous',
  'guest',
]);

/** Remember 404/errored handles per session so re-renders never flash or retry dead URLs. */
const FAILED_GITHUB_HANDLES = new Set<string>();

/** Editorial & Tiranga-harmonized palette for deterministic geometric identicons. */
const IDENTICON_PALETTE: ReadonlyArray<{ fg: string; bg: string }> = [
  { fg: '#C85A32', bg: '#FBF1EC' }, // terracotta / saffron-rust
  { fg: '#138808', bg: '#EBF6EA' }, // India green
  { fg: '#A84F00', bg: '#FDF3E7' }, // deep saffron amber
  { fg: '#2D5A88', bg: '#EDF3FA' }, // civic slate blue
  { fg: '#8B6D1F', bg: '#FAF5E4' }, // warm brass / gold
  { fg: '#7A3B5E', bg: '#F7EEF3' }, // muted plum
  { fg: '#2C6B5C', bg: '#EAF5F2' }, // deep teal
  { fg: '#4A463E', bg: '#F2F0EB' }, // warm charcoal stone
];

export function normalizeHandle(raw: string): string {
  return (raw || '').trim().replace(/^@+/, '');
}

export function isValidGitHubHandle(raw: string): boolean {
  const clean = normalizeHandle(raw);
  if (!clean) return false;
  if (SYNTHETIC_HANDLES.has(clean.toLowerCase())) return false;
  return GITHUB_HANDLE_RE.test(clean);
}

/** Fast deterministic 32-bit FNV-1a hash for identicon symmetry + hue selection. */
function fnv1a32(input: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/**
 * Generates a crisp symmetric 5×5 GitHub-style geometric pixel matrix from a handle.
 * Left 3 columns (15 bits) are mirrored across the center axis for recognizable balance.
 */
function buildIdenticonCells(seed: string): {
  cells: Array<{ x: number; y: number }>;
  fg: string;
  bg: string;
} {
  const h1 = fnv1a32(seed.toLowerCase());
  const h2 = fnv1a32(`${seed.toLowerCase()}::aw-v1`);
  const palette = IDENTICON_PALETTE[h1 % IDENTICON_PALETTE.length];
  const cells: Array<{ x: number; y: number }> = [];

  for (let row = 0; row < 5; row++) {
    for (let col = 0; col < 3; col++) {
      const bitIndex = row * 3 + col;
      const on = ((h2 >>> bitIndex) & 1) === 1;
      if (on) {
        cells.push({ x: col, y: row });
        if (col < 2) {
          cells.push({ x: 4 - col, y: row });
        }
      }
    }
  }

  // Ensure identicon is never blank even on degenerate hashes
  if (cells.length < 4) {
    cells.push({ x: 2, y: 1 }, { x: 1, y: 2 }, { x: 3, y: 2 }, { x: 2, y: 3 });
  }

  return { cells, fg: palette.fg, bg: palette.bg };
}

export interface GitHubAvatarProps {
  /** Contributor handle, with or without leading '@'. */
  handle: string;
  /** Rendered diameter in CSS pixels. Defaults to 24. */
  size?: number;
  /** Optional extra CSS class on the outer wrapper. */
  className?: string;
  /** Accessible alt label (defaults to decorative empty alt if omitted). */
  alt?: string;
  /** Optional live status badge rendered at the bottom-right corner. */
  statusDot?: 'working' | 'verifying' | 'recent' | 'idle' | null;
  /** Render a subtle emerald live ring around the avatar when actively working. */
  activeRing?: boolean;
}

/**
 * Renders a contributor's GitHub avatar (`https://github.com/<handle>.png?size=64`)
 * for valid GitHub-like handles, layered cleanly over a deterministic 5×5 geometric
 * SVG identicon fallback so broken image icons never appear and layout never jumps.
 */
export function GitHubAvatar({
  handle,
  size = 24,
  className = '',
  alt = '',
  statusDot = null,
  activeRing = false,
}: GitHubAvatarProps) {
  const cleanHandle = normalizeHandle(handle);
  const cacheKey = cleanHandle.toLowerCase();
  const canTryGitHub = isValidGitHubHandle(cleanHandle) && !FAILED_GITHUB_HANDLES.has(cacheKey);

  const [loadedHandle, setLoadedHandle] = useState<string | null>(null);
  const [failedHandle, setFailedHandle] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  const isLoaded = loadedHandle === cacheKey;
  const hasFailed = failedHandle === cacheKey || FAILED_GITHUB_HANDLES.has(cacheKey);
  const showImg = canTryGitHub && !hasFailed;

  const identicon = useMemo(() => buildIdenticonCells(cleanHandle || 'aw'), [cleanHandle]);

  const requestSize = size > 36 ? 96 : 64;
  const imgUrl = showImg
    ? `https://github.com/${encodeURIComponent(cleanHandle)}.png?size=${requestSize}`
    : null;

  useEffect(() => {
    const img = imgRef.current;
    if (!img || !imgUrl) return;
    if (img.complete) {
      if (img.naturalWidth > 0) {
        setLoadedHandle(cacheKey);
      } else {
        FAILED_GITHUB_HANDLES.add(cacheKey);
        setFailedHandle(cacheKey);
      }
    }
  }, [imgUrl, cacheKey]);

  const wrapperClasses = [
    'lsv-avatar',
    activeRing ? 'lsv-avatar--active' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <span
      className={wrapperClasses}
      style={{ width: size, height: size }}
      aria-hidden={alt ? undefined : true}
    >
      <span className="lsv-avatar-clip">
        <svg
          className="lsv-avatar-identicon"
          viewBox="0 0 7 7"
          width={size}
          height={size}
          aria-hidden="true"
        >
          <rect width="7" height="7" fill={identicon.bg} />
          {identicon.cells.map((cell, idx) => (
            <rect
              key={idx}
              x={cell.x + 1}
              y={cell.y + 1}
              width="1"
              height="1"
              rx="0.16"
              fill={identicon.fg}
            />
          ))}
        </svg>

        {imgUrl && (
          <img
            ref={imgRef}
            key={imgUrl}
            src={imgUrl}
            alt={alt}
            width={size}
            height={size}
            decoding="async"
            referrerPolicy="no-referrer"
            className={`lsv-avatar-img${isLoaded ? ' is-loaded' : ''}`}
            onLoad={(e) => {
              if (e.currentTarget.naturalWidth > 0) {
                setLoadedHandle(cacheKey);
              } else {
                FAILED_GITHUB_HANDLES.add(cacheKey);
                setFailedHandle(cacheKey);
              }
            }}
            onError={() => {
              FAILED_GITHUB_HANDLES.add(cacheKey);
              setFailedHandle(cacheKey);
            }}
          />
        )}
      </span>

      {statusDot && (
        <span
          className={`lsv-avatar-dot lsv-avatar-dot--${statusDot}`}
          aria-hidden="true"
        />
      )}
    </span>
  );
}
