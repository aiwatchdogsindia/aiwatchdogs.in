import { ArrowUpRight, ChevronLeft, ChevronRight, ExternalLink, Minus, Plus, RotateCcw, Search, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from '../router';
import type { GraphData, GraphNode } from '../types';
import { formatInrCr, isUnverifiedStatus, pillarLabel, pillarTint, projectCodeFor, safeExternalUrl, statusMeta } from '../utils';

interface ObservatoryMapProps {
  graph: GraphData | null;
  loading?: boolean;
  agentsActive?: number;
  verifiedCount?: number;
  missionsCount?: number;
  refreshTick?: number;
  onSelectFinding: (id: string | number) => void;
}

interface WorldNode {
  node: GraphNode;
  clusterKey: string;
  clusterLabel: string;
  pillar: string;
  x: number;
  y: number;
  radius: number;
  kind: 'verified' | 'review' | 'cleared' | 'disputed' | 'mission' | 'source' | 'ministry';
  isCommunity: boolean;
}

interface ClusterInfo {
  key: string;
  label: string;
  shortCode: string;
  pillar: string;
  tint: ReturnType<typeof pillarTint>;
  cx: number;
  cy: number;
  radius: number;
  verifiedCount: number;
  reviewCount: number;
  sourceCount: number;
  totalCount: number;
}

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

function fnv1a(str: string): number {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295;
}

function classifyNodeKind(node: GraphNode): WorldNode['kind'] {
  const t = (node.type || '').toLowerCase();
  const s = (node.status || '').toLowerCase();
  if (t.includes('mission')) return 'mission';
  if (t.includes('source')) return 'source';
  if (t.includes('ministry') || t.includes('jurisdiction') || t.includes('authority')) return 'ministry';
  if (s === 'verified' || s === 'rti_filed' || s === 'official_response_logged') return 'verified';
  if (s === 'cleared' || s === 'resolved_explained') return 'cleared';
  if (s === 'disputed' || s === 'retracted_false_positive') return 'disputed';
  return 'review';
}


export function ObservatoryMap({
  graph,
  loading = false,
  agentsActive = 0,
  verifiedCount = 0,
  missionsCount = 8,
  refreshTick = 0,
  onSelectFinding,
}: ObservatoryMapProps) {
  const { navigate } = useRouter();
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const minimapRef = useRef<HTMLCanvasElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const [search, setSearch] = useState('');
  const [searchFocused, setSearchFocused] = useState(false);
  const [activeMatchIdx, setActiveMatchIdx] = useState(0);
  const searchWrapRef = useRef<HTMLDivElement | null>(null);
  const requestDrawRef = useRef<() => void>(() => {});
  const inViewRef = useRef<boolean>(true);
  const tabVisibleRef = useRef<boolean>(
    typeof document === 'undefined' ? true : document.visibilityState !== 'hidden',
  );
  const reducedMotionRef = useRef<boolean>(
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false,
  );
  const [activeClusterKey, setActiveClusterKey] = useState<string | null>(null);
  const [hoveredNode, setHoveredNode] = useState<{ wn: WorldNode; sx: number; sy: number } | null>(null);
  const [pinnedId, setPinnedId] = useState<string | null>(null);
  const [wheelHintVisible, setWheelHintVisible] = useState(false);

  // Camera state kept in ref for silky 60fps canvas animation
  const camRef = useRef({
    x: 0,
    y: 0,
    zoom: 1,
    targetX: 0,
    targetY: 0,
    targetZoom: 1,
  });
  const dragRef = useRef({
    active: false,
    moved: false,
    startX: 0,
    startY: 0,
    camStartX: 0,
    camStartY: 0,
  });
  const igniteRef = useRef<{ startedAt: number; nodeId: string | null }>({
    startedAt: 0,
    nodeId: null,
  });
  const hoveredIdRef = useRef<string | null>(null);
  const pinnedIdRef = useRef<string | null>(null);
  const wheelHintShownRef = useRef(false);

  pinnedIdRef.current = pinnedId;

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];

  // Deterministic mission/pillar spatial constellation layout using golden-angle phyllotaxis
  const world = useMemo(() => {
    const missionNodes = nodes.filter((n) => classifyNodeKind(n) === 'mission');
    const edgeAdj = new Map<string, string[]>();
    for (const e of edges) {
      if (!edgeAdj.has(e.source)) edgeAdj.set(e.source, []);
      if (!edgeAdj.has(e.target)) edgeAdj.set(e.target, []);
      edgeAdj.get(e.source)!.push(e.target);
      edgeAdj.get(e.target)!.push(e.source);
    }

    // Determine canonical cluster keys (prefer mission slugs, fallback to pillar)
    const clusterList: Array<{ key: string; label: string; shortCode: string; pillar: string }> = [];
    const seenKeys = new Set<string>();
    const missionIdToSlug = new Map<string, string>();

    for (const mn of missionNodes) {
      const slug = String(mn.meta?.slug || mn.id.replace(/^mission:/, ''));
      missionIdToSlug.set(mn.id, slug);
      if (!seenKeys.has(slug)) {
        seenKeys.add(slug);
        clusterList.push({
          key: slug,
          label: mn.label,
          shortCode: projectCodeFor({
            project_code: mn.meta?.project_code ? String(mn.meta.project_code) : undefined,
            slug,
            methodology_code: mn.meta?.methodology_code ? String(mn.meta.methodology_code) : undefined,
          }),
          pillar: mn.pillar || 'procurement',
        });
      }
    }

    if (clusterList.length === 0) {
      const pillars = Array.from(new Set(nodes.map((n) => n.pillar || 'procurement')));
      for (const p of pillars) {
        seenKeys.add(p);
        clusterList.push({
          key: p,
          label: pillarLabel(p),
          shortCode: p.slice(0, 3).toUpperCase(),
          pillar: p,
        });
      }
    }

    // Place cluster centers in a spacious 2-row constellation across world space
    const cCount = Math.max(1, clusterList.length);
    const cols = Math.min(4, Math.max(2, Math.ceil(cCount / 2)));
    const rows = Math.ceil(cCount / cols);
    const spanX = 960;
    const spanY = rows > 1 ? 370 : 120;

    const clusterCenters = new Map<string, { cx: number; cy: number; meta: (typeof clusterList)[number] }>();
    clusterList.forEach((c, idx) => {
      const r = Math.floor(idx / cols);
      const col = idx % cols;
      const itemsInRow = r === rows - 1 ? cCount - r * cols : cols;
      const rowOffsetX = itemsInRow < cols ? ((cols - itemsInRow) * (spanX / Math.max(1, cols - 1))) / 2 : 0;
      const cx =
        cols === 1
          ? 0
          : -spanX / 2 + (col * spanX) / (cols - 1) + rowOffsetX + (r % 2 === 1 ? 38 : -24);
      const cy = rows === 1 ? 0 : -spanY / 2 + (r * spanY) / (rows - 1) + (col % 2 === 0 ? -26 : 26);
      clusterCenters.set(c.key, { cx, cy, meta: c });
    });

    // Assign every node to a cluster key
    const grouped = new Map<string, GraphNode[]>();
    const defaultKey = clusterList[0]?.key || 'procurement';

    for (const n of nodes) {
      let cKey = '';
      if (classifyNodeKind(n) === 'mission') {
        cKey = String(n.meta?.slug || n.id.replace(/^mission:/, ''));
      } else if (n.meta?.mission_slug && clusterCenters.has(String(n.meta.mission_slug))) {
        cKey = String(n.meta.mission_slug);
      } else {
        // Check adjacent mission node
        const neighbors = edgeAdj.get(n.id) || [];
        for (const nb of neighbors) {
          const slug = missionIdToSlug.get(nb);
          if (slug && clusterCenters.has(slug)) {
            cKey = slug;
            break;
          }
        }
      }
      if (!cKey || !clusterCenters.has(cKey)) {
        const byPillar = clusterList.find((c) => c.pillar === n.pillar);
        cKey = byPillar?.key || defaultKey;
      }
      if (!grouped.has(cKey)) grouped.set(cKey, []);
      grouped.get(cKey)!.push(n);
    }

    const worldNodes: WorldNode[] = [];
    const byId = new Map<string, WorldNode>();
    const clusters: ClusterInfo[] = [];

    for (const c of clusterList) {
      const center = clusterCenters.get(c.key)!;
      const members = (grouped.get(c.key) || []).slice().sort((a, b) => {
        const rank = (x: GraphNode) => {
          const k = classifyNodeKind(x);
          if (k === 'mission') return 0;
          if (k === 'verified') return 1;
          if (k === 'review') return 2;
          if (k === 'cleared') return 3;
          if (k === 'disputed') return 4;
          if (k === 'source') return 5;
          return 6;
        };
        return rank(a) - rank(b) || a.id.localeCompare(b.id);
      });

      let maxR = 64;
      let vCount = 0;
      let rCount = 0;
      let sCount = 0;

      members.forEach((node, i) => {
        const kind = classifyNodeKind(node);
        if (kind === 'verified') vCount += 1;
        if (kind === 'review') rCount += 1;
        if (kind === 'source') sCount += 1;

        let x = center.cx;
        let y = center.cy;
        if (i > 0) {
          const spacing = 34;
          const dist = spacing * Math.sqrt(i + 0.65);
          const theta = i * GOLDEN_ANGLE + fnv1a(node.id) * 0.45;
          const jx = (fnv1a(`${node.id}:x`) - 0.5) * 10;
          const jy = (fnv1a(`${node.id}:y`) - 0.5) * 10;
          x = center.cx + Math.cos(theta) * dist + jx;
          y = center.cy + Math.sin(theta) * dist + jy;
          maxR = Math.max(maxR, dist + 28);
        }

        const radius =
          kind === 'mission'
            ? 8.5
            : kind === 'verified'
              ? 7.2
              : kind === 'review'
                ? 6.6
                : kind === 'disputed' || kind === 'cleared'
                  ? 6.4
                  : 5.5;

        const wn: WorldNode = {
          node,
          clusterKey: c.key,
          clusterLabel: c.label,
          pillar: node.pillar || c.pillar,
          x,
          y,
          radius,
          kind,
          isCommunity: Boolean(node.meta?.is_community),
        };
        worldNodes.push(wn);
        byId.set(node.id, wn);
      });

      clusters.push({
        key: c.key,
        label: c.label,
        shortCode: c.shortCode,
        pillar: c.pillar,
        tint: pillarTint(c.pillar),
        cx: center.cx,
        cy: center.cy,
        radius: Math.min(138, Math.max(76, maxR)),
        verifiedCount: vCount,
        reviewCount: rCount,
        sourceCount: sCount,
        totalCount: members.length,
      });
    }

    // World bounds for minimap & fit
    let minX = -620;
    let maxX = 620;
    let minY = -340;
    let maxY = 340;
    for (const c of clusters) {
      minX = Math.min(minX, c.cx - c.radius - 40);
      maxX = Math.max(maxX, c.cx + c.radius + 40);
      minY = Math.min(minY, c.cy - c.radius - 40);
      maxY = Math.max(maxY, c.cy + c.radius + 40);
    }

    return { worldNodes, byId, clusters, bounds: { minX, maxX, minY, maxY } };
  }, [nodes, edges]);

  // Trigger transient golden SSE ignite ripple when refreshTick bumps
  useEffect(() => {
    if (refreshTick <= 0) return;
    const verifiedNode = world.worldNodes.find((w) => w.kind === 'verified');
    if (verifiedNode) {
      igniteRef.current = { startedAt: performance.now(), nodeId: verifiedNode.node.id };
    }
  }, [refreshTick, world.worldNodes]);

  // Helper: fit entire world smoothly
  const fitAll = useCallback(
    (instant = false) => {
      const el = wrapRef.current;
      const w = el ? el.clientWidth : 1200;
      const h = el ? el.clientHeight : 700;
      const bw = world.bounds.maxX - world.bounds.minX;
      const bh = world.bounds.maxY - world.bounds.minY;
      const z = Math.max(0.52, Math.min(1.15, Math.min((w * 0.88) / bw, (h * 0.72) / bh)));
      const cx = (world.bounds.minX + world.bounds.maxX) / 2;
      const cy = (world.bounds.minY + world.bounds.maxY) / 2 - 14;
      camRef.current.targetX = cx;
      camRef.current.targetY = cy;
      camRef.current.targetZoom = z;
      if (instant) {
        camRef.current.x = cx;
        camRef.current.y = cy;
        camRef.current.zoom = z;
      }
      setActiveClusterKey(null);
    },
    [world.bounds],
  );

  // Initial fit on mount / graph change
  useEffect(() => {
    fitAll(true);
  }, [fitAll]);

  const flyTo = useCallback((wx: number, wy: number, zoom: number) => {
    const clampedZoom = Math.max(0.45, Math.min(3.2, zoom));
    camRef.current.targetX = wx;
    camRef.current.targetY = wy;
    camRef.current.targetZoom = clampedZoom;
    if (reducedMotionRef.current) {
      camRef.current.x = wx;
      camRef.current.y = wy;
      camRef.current.zoom = clampedZoom;
    }
    requestDrawRef.current();
  }, []);

  const focusNode = useCallback(
    (wn: WorldNode) => {
      setPinnedId(wn.node.id);
      setActiveClusterKey(wn.clusterKey);
      // Offset camera slightly right so node sits visibly left of the right-hand .map-sheet!
      const isNarrow = (wrapRef.current?.clientWidth ?? 1200) < 760;
      const zoom = Math.max(1.55, camRef.current.targetZoom);
      const offsetX = isNarrow ? 0 : 125 / zoom;
      flyTo(wn.x + offsetX, wn.y, zoom);
    },
    [flyTo],
  );

  const focusCluster = useCallback(
    (cluster: ClusterInfo) => {
      setActiveClusterKey(cluster.key);
      setPinnedId(null);
      flyTo(cluster.cx, cluster.cy, 1.55);
    },
    [flyTo],
  );

  // `/` keyboard shortcut focuses Observatory search; `Escape` resets
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (e.key === '/' && tag !== 'input' && tag !== 'textarea' && tag !== 'select') {
        if (searchInputRef.current) {
          e.preventDefault();
          searchInputRef.current.focus();
          searchInputRef.current.select();
        }
      } else if (e.key === 'Escape') {
        if (searchFocused) {
          searchInputRef.current?.blur();
          setSearchFocused(false);
        } else if (pinnedIdRef.current) {
          setPinnedId(null);
        } else if (activeClusterKey) {
          fitAll(false);
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [activeClusterKey, fitAll, searchFocused]);

  // Search combobox matches
  const searchMatches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) {
      const findingHits = world.worldNodes.filter((w) => w.kind === 'verified' || w.kind === 'review');
      if (findingHits.length > 0) return findingHits.slice(0, 7);
      return world.worldNodes.filter((w) => w.kind === 'mission' || w.kind === 'source').slice(0, 8);
    }
    return world.worldNodes
      .filter((w) => {
        const metaStr = w.node.meta ? JSON.stringify(w.node.meta).toLowerCase() : '';
        return (
          w.node.label.toLowerCase().includes(q) ||
          w.clusterLabel.toLowerCase().includes(q) ||
          w.pillar.toLowerCase().includes(q) ||
          (w.node.status || '').toLowerCase().includes(q) ||
          metaStr.includes(q)
        );
      })
      .slice(0, 9);
  }, [search, world.worldNodes]);

  // Screen <-> World coordinate transforms
  const worldToScreen = useCallback((wx: number, wy: number) => {
    const el = wrapRef.current;
    const w = el ? el.clientWidth : 1200;
    const h = el ? el.clientHeight : 700;
    const c = camRef.current;
    return {
      sx: (wx - c.x) * c.zoom + w / 2,
      sy: (wy - c.y) * c.zoom + h / 2,
    };
  }, []);

  const screenToWorld = useCallback((sx: number, sy: number) => {
    const el = wrapRef.current;
    const w = el ? el.clientWidth : 1200;
    const h = el ? el.clientHeight : 700;
    const c = camRef.current;
    return {
      wx: (sx - w / 2) / c.zoom + c.x,
      wy: (sy - h / 2) / c.zoom + c.y,
    };
  }, []);

  const hitTest = useCallback(
    (sx: number, sy: number): WorldNode | null => {
      const { wx, wy } = screenToWorld(sx, sy);
      const hitPad = 14 / Math.max(0.5, camRef.current.zoom);
      let best: WorldNode | null = null;
      let bestD = Infinity;
      for (const wn of world.worldNodes) {
        const dx = wn.x - wx;
        const dy = wn.y - wy;
        const d = Math.hypot(dx, dy);
        if (d <= wn.radius + hitPad && d < bestD) {
          best = wn;
          bestD = d;
        }
      }
      return best;
    },
    [screenToWorld, world.worldNodes],
  );

  // Main HTML5 Canvas Render Loop + Minimap Render
  // Pauses cleanly when offscreen (IntersectionObserver), tab hidden (visibilitychange), or prefers-reduced-motion.
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;


    let rafId = 0;
    let disposed = false;

    const shouldLoop = () =>
      !disposed && inViewRef.current && tabVisibleRef.current && !reducedMotionRef.current;

    const draw = (now: number) => {
      rafId = 0;
      if (disposed) return;

      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = wrap.clientWidth;
      const h = wrap.clientHeight;
      if (w <= 0 || h <= 0) return;
      if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
        canvas.width = Math.floor(w * dpr);
        canvas.height = Math.floor(h * dpr);
      }

      // Smooth camera interpolation (snaps cleanly under prefers-reduced-motion)
      const cam = camRef.current;
      if (reducedMotionRef.current) {
        cam.x = cam.targetX;
        cam.y = cam.targetY;
        cam.zoom = cam.targetZoom;
      } else {
        cam.x += (cam.targetX - cam.x) * 0.14;
        cam.y += (cam.targetY - cam.y) * 0.14;
        cam.zoom += (cam.targetZoom - cam.zoom) * 0.14;
      }

      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, w, h);

      // Transform into world coordinates
      ctx.translate(w / 2, h / 2);
      ctx.scale(cam.zoom, cam.zoom);
      ctx.translate(-cam.x, -cam.y);

      const focusId = hoveredIdRef.current || pinnedIdRef.current;

      // 1. Cluster radial hazes & dashed perimeter clouds
      for (const cl of world.clusters) {
        ctx.save();
        ctx.setLineDash([3 / cam.zoom, 5 / cam.zoom]);
        ctx.strokeStyle = `rgba(${cl.tint.rgb}, 0.24)`;
        ctx.lineWidth = 1 / cam.zoom;
        ctx.beginPath();
        ctx.arc(cl.cx, cl.cy, cl.radius, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();

        // Cluster label below cloud
        ctx.save();
        ctx.font = `400 ${Math.max(10, 11.5 / Math.sqrt(cam.zoom))}px "Geist Serif", Georgia, serif`;
        ctx.fillStyle = 'rgba(27, 26, 23, 0.86)';
        ctx.textAlign = 'center';
        const shortLabel = `${cl.shortCode}, ${cl.label.length > 26 ? `${cl.label.slice(0, 25)}…` : cl.label}`.toUpperCase();
        ctx.fillText(shortLabel, cl.cx, cl.cy + cl.radius + 16 / Math.sqrt(cam.zoom));

        ctx.font = `400 ${Math.max(9.5, 11 / Math.sqrt(cam.zoom))}px "Geist Serif", Georgia, serif`;
        ctx.fillStyle = 'rgba(27, 26, 23, 0.62)';
        const clSub =
          cl.verifiedCount > 0 || cl.reviewCount > 0
            ? `${cl.verifiedCount} verified, ${cl.reviewCount} in review`
            : `${cl.sourceCount > 0 ? `${cl.sourceCount} official source${cl.sourceCount === 1 ? '' : 's'}` : `${cl.totalCount} mapped`}, ready to audit`;
        ctx.fillText(clSub, cl.cx, cl.cy + cl.radius + 30 / Math.sqrt(cam.zoom));
        ctx.restore();
      }

      // 2. Curved Edges
      for (const e of edges) {
        const s = world.byId.get(e.source);
        const t = world.byId.get(e.target);
        if (!s || !t) continue;
        const isInc = focusId !== null && (e.source === focusId || e.target === focusId);
        const crossCluster = s.clusterKey !== t.clusterKey;

        const mx = (s.x + t.x) / 2;
        const my = (s.y + t.y) / 2;
        const dx = t.x - s.x;
        const dy = t.y - s.y;
        const bend = crossCluster ? 0.16 : 0.08;
        const cx = mx - dy * bend;
        const cy = my + dx * bend;

        ctx.save();
        ctx.beginPath();
        ctx.moveTo(s.x, s.y);
        ctx.quadraticCurveTo(cx, cy, t.x, t.y);

        if (isInc) {
          ctx.strokeStyle = 'rgba(27, 26, 23, 0.72)';
          ctx.lineWidth = 1.9 / Math.sqrt(cam.zoom);
        } else if (s.kind === 'disputed' || t.kind === 'disputed') {
          ctx.strokeStyle = '#4A2622';
          ctx.setLineDash([3, 4]);
          ctx.lineWidth = 1.1 / Math.sqrt(cam.zoom);
        } else if (s.kind === 'review' || t.kind === 'review') {
          ctx.strokeStyle = 'rgba(27, 26, 23, 0.22)';
          ctx.setLineDash([2, 5]);
          ctx.lineWidth = 1 / Math.sqrt(cam.zoom);
        } else {
          ctx.strokeStyle = crossCluster ? 'rgba(27,26,23,0.12)' : 'rgba(27,26,23,0.38)';
          ctx.lineWidth = (crossCluster ? 1 : 1.25) / Math.sqrt(cam.zoom);
        }
        ctx.stroke();
        ctx.restore();
      }

      // 3. Nodes (Strict Epistemic Grammar on Dark!)
      const tSec = now * 0.001;
      for (const wn of world.worldNodes) {
        const isPinned = pinnedIdRef.current === wn.node.id;
        const isHov = hoveredIdRef.current === wn.node.id;
        const r = wn.radius * (isPinned || isHov ? 1.22 : 1);

        // Ignite SSE golden ripple if active
        if (igniteRef.current.nodeId === wn.node.id && now - igniteRef.current.startedAt < 2600) {
          const p = (now - igniteRef.current.startedAt) / 2600;
          for (let ring = 0; ring < 2; ring++) {
            const rp = Math.max(0, p - ring * 0.22);
            if (rp > 0 && rp < 1) {
              ctx.beginPath();
              ctx.arc(wn.x, wn.y, r + rp * 44, 0, Math.PI * 2);
              ctx.strokeStyle = `rgba(27, 26, 23, ${(1 - rp) * 0.6})`;
              ctx.lineWidth = 1.6 / cam.zoom;
              ctx.stroke();
            }
          }
        }

        // Community public contributor outer ink ring
        if (wn.isCommunity) {
          ctx.beginPath();
          ctx.arc(wn.x, wn.y, r + 3.6, 0, Math.PI * 2);
          ctx.strokeStyle = '#1B1A17';
          ctx.lineWidth = 2.4 / Math.sqrt(cam.zoom);
          ctx.stroke();
        }

        // Verified node: warm-cream #FFFFFF solid dot + pre-rendered radial glow (ONLY resting glow!)
        if (wn.kind === 'verified') {
          ctx.beginPath();
          ctx.arc(wn.x, wn.y, r, 0, Math.PI * 2);
          ctx.fillStyle = '#FFFFFF';
          ctx.fill();
        } else if (wn.kind === 'review') {
          // In Peer Review / Unverified: HOLLOW dashed #CFE8CA + orbiting cyan agent comet!
          ctx.save();
          ctx.beginPath();
          ctx.arc(wn.x, wn.y, r, 0, Math.PI * 2);
          ctx.fillStyle = '#F28A1E';
          ctx.fill();
          ctx.setLineDash([2.2, 2.2]);
          ctx.strokeStyle = '#3A2E1E';
          ctx.lineWidth = 1.5;
          ctx.stroke();
          ctx.restore();

          // Orbiting cyan agent-at-work comet (#FFD9B3 core + #FFB866 tail)
          const phase = tSec * 1.45 + fnv1a(wn.node.id) * Math.PI * 2;
          const orbR = r + 5.2;
          const cx = wn.x + Math.cos(phase) * orbR;
          const cy = wn.y + Math.sin(phase) * orbR;
          ctx.beginPath();
          ctx.arc(wn.x, wn.y, orbR, phase - 1.15, phase);
          ctx.strokeStyle = 'rgba(27, 26, 23, 0.5)';
          ctx.lineWidth = 1.4;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(cx, cy, 1.9, 0, Math.PI * 2);
          ctx.fillStyle = '#FFFFFF';
          ctx.fill();
        } else if (wn.kind === 'disputed') {
          // Disputed: #053402 fill + #C2412D ring + internal × cross
          ctx.beginPath();
          ctx.arc(wn.x, wn.y, r, 0, Math.PI * 2);
          ctx.fillStyle = '#F28A1E';
          ctx.fill();
          ctx.strokeStyle = '#8A1C0C';
          ctx.lineWidth = 1.6;
          ctx.stroke();
          const d = r * 0.48;
          ctx.beginPath();
          ctx.moveTo(wn.x - d, wn.y - d);
          ctx.lineTo(wn.x + d, wn.y + d);
          ctx.moveTo(wn.x + d, wn.y - d);
          ctx.lineTo(wn.x - d, wn.y + d);
          ctx.strokeStyle = '#8A1C0C';
          ctx.lineWidth = 1.4;
          ctx.stroke();
        } else if (wn.kind === 'cleared') {
          // Cleared & Explained: #053402 fill + #8FC287 ring + horizontal stop bar −
          ctx.beginPath();
          ctx.arc(wn.x, wn.y, r, 0, Math.PI * 2);
          ctx.fillStyle = '#F28A1E';
          ctx.fill();
          ctx.strokeStyle = '#0F7306';
          ctx.lineWidth = 1.5;
          ctx.stroke();
          ctx.beginPath();
          ctx.moveTo(wn.x - r * 0.52, wn.y);
          ctx.lineTo(wn.x + r * 0.52, wn.y);
          ctx.strokeStyle = '#3A2E1E';
          ctx.lineWidth = 1.5;
          ctx.stroke();
        } else if (wn.kind === 'mission') {
          // Mission core node: warm amber outline
          ctx.beginPath();
          ctx.arc(wn.x, wn.y, r, 0, Math.PI * 2);
          ctx.fillStyle = '#1B1A17';
          ctx.fill();
          ctx.strokeStyle = '#FFFFFF';
          ctx.lineWidth = 1.8;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(wn.x, wn.y, 2.4, 0, Math.PI * 2);
          ctx.fillStyle = '#FFFFFF';
          ctx.fill();
        } else if (wn.kind === 'source') {
          // Official .gov.in source: quiet cobalt diamond
          ctx.save();
          ctx.translate(wn.x, wn.y);
          ctx.rotate(Math.PI / 4);
          ctx.fillStyle = '#F28A1E';
          ctx.strokeStyle = '#3A2E1E';
          ctx.lineWidth = 1.3;
          ctx.fillRect(-r * 0.72, -r * 0.72, r * 1.44, r * 1.44);
          ctx.strokeRect(-r * 0.72, -r * 0.72, r * 1.44, r * 1.44);
          ctx.restore();
        } else {
          // Ministry / jurisdiction anchor
          ctx.beginPath();
          ctx.arc(wn.x, wn.y, r, 0, Math.PI * 2);
          ctx.fillStyle = '#F4F4F4';
          ctx.fill();
          ctx.strokeStyle = '#1B1A17';
          ctx.lineWidth = 1.25;
          ctx.stroke();
        }

        // Pinned / hovered ring
        if (isPinned || isHov) {
          ctx.beginPath();
          ctx.arc(wn.x, wn.y, r + 5.5, 0, Math.PI * 2);
          ctx.strokeStyle = isPinned ? '#1B1A17' : 'rgba(255,255,255,0.75)';
          ctx.lineWidth = 1.5 / Math.sqrt(cam.zoom);
          ctx.stroke();
        }

        // Zoomed-in crisp node labels
        if (cam.zoom >= 1.32 || isPinned || isHov) {
          ctx.save();
          ctx.font = `400 ${12.5 / Math.sqrt(cam.zoom)}px "Geist Serif", Georgia, serif`;
          ctx.fillStyle = isPinned || isHov ? '#1B1A17' : 'rgba(27,26,23,0.82)';
          ctx.textAlign = 'left';
          const lbl = wn.node.label.length > 28 ? `${wn.node.label.slice(0, 27)}…` : wn.node.label;
          ctx.fillText(lbl, wn.x + r + 7, wn.y + 3.5 / Math.sqrt(cam.zoom));
          ctx.restore();
        }
      }

      ctx.restore();

      // 4. Interactive 150×94 Minimap
      const mm = minimapRef.current;
      if (mm) {
        const mw = 150;
        const mh = 94;
        if (mm.width !== mw * dpr || mm.height !== mh * dpr) {
          mm.width = mw * dpr;
          mm.height = mh * dpr;
        }
        const mctx = mm.getContext('2d');
        if (mctx) {
          mctx.save();
          mctx.scale(dpr, dpr);
          mctx.clearRect(0, 0, mw, mh);

          const { minX, maxX, minY, maxY } = world.bounds;
          const bw = Math.max(1, maxX - minX);
          const bh = Math.max(1, maxY - minY);
          const toMx = (wx: number) => 8 + ((wx - minX) / bw) * (mw - 16);
          const toMy = (wy: number) => 8 + ((wy - minY) / bh) * (mh - 16);

          for (const wn of world.worldNodes) {
            mctx.fillStyle =
              wn.kind === 'verified'
                ? '#0F7306'
                : wn.kind === 'mission'
                  ? '#FF9933'
                  : wn.kind === 'disputed'
                    ? '#8A1C0C'
                    : '#6E4A1C';
            mctx.fillRect(toMx(wn.x) - 1, toMy(wn.y) - 1, 2.2, 2.2);
          }

          // Current viewport rectangle
          const vx1 = toMx(cam.x - w / (2 * cam.zoom));
          const vy1 = toMy(cam.y - h / (2 * cam.zoom));
          const vx2 = toMx(cam.x + w / (2 * cam.zoom));
          const vy2 = toMy(cam.y + h / (2 * cam.zoom));
          mctx.strokeStyle = 'rgba(27, 26, 23, 0.8)';
          mctx.fillStyle = 'rgba(27, 26, 23, 0.08)';
          mctx.lineWidth = 1;
          mctx.fillRect(vx1, vy1, Math.max(8, vx2 - vx1), Math.max(6, vy2 - vy1));
          mctx.strokeRect(vx1, vy1, Math.max(8, vx2 - vx1), Math.max(6, vy2 - vy1));
          mctx.restore();
        }
      }

      if (shouldLoop()) {
        rafId = window.requestAnimationFrame(draw);
      }
    };

    const scheduleDraw = () => {
      if (disposed || rafId !== 0) return;
      if (!inViewRef.current || !tabVisibleRef.current) return;
      rafId = window.requestAnimationFrame(draw);
    };

    requestDrawRef.current = scheduleDraw;

    // Pause animation when scrolled offscreen
    let io: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== 'undefined') {
      io = new IntersectionObserver(
        ([entry]) => {
          inViewRef.current = Boolean(entry?.isIntersecting);
          wrap.dataset.mapPaused = shouldLoop() ? 'false' : 'true';
          if (inViewRef.current && tabVisibleRef.current) {
            scheduleDraw();
          } else if (rafId !== 0) {
            window.cancelAnimationFrame(rafId);
            rafId = 0;
          }
        },
        { threshold: 0.01 },
      );
      io.observe(wrap);
    }

    const onVisibility = () => {
      tabVisibleRef.current = document.visibilityState !== 'hidden';
      wrap.dataset.mapPaused = shouldLoop() ? 'false' : 'true';
      if (tabVisibleRef.current && inViewRef.current) {
        scheduleDraw();
      } else if (rafId !== 0) {
        window.cancelAnimationFrame(rafId);
        rafId = 0;
      }
    };
    document.addEventListener('visibilitychange', onVisibility);

    const mq =
      typeof window.matchMedia === 'function'
        ? window.matchMedia('(prefers-reduced-motion: reduce)')
        : null;
    const onMotionChange = () => {
      reducedMotionRef.current = Boolean(mq?.matches);
      wrap.dataset.mapPaused = shouldLoop() ? 'false' : 'true';
      if (rafId !== 0) {
        window.cancelAnimationFrame(rafId);
        rafId = 0;
      }
      scheduleDraw();
    };
    mq?.addEventListener?.('change', onMotionChange);

    const onResize = () => scheduleDraw();
    window.addEventListener('resize', onResize);
    document.fonts?.ready.then(() => scheduleDraw()).catch(() => {});

    wrap.dataset.mapPaused = shouldLoop() ? 'false' : 'true';
    scheduleDraw();

    return () => {
      disposed = true;
      requestDrawRef.current = () => {};
      if (rafId !== 0) window.cancelAnimationFrame(rafId);
      io?.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
      mq?.removeEventListener?.('change', onMotionChange);
      window.removeEventListener('resize', onResize);
    };
  }, [edges, world]);

  // Pointer & Wheel Handlers
  const handlePointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    (e.currentTarget as HTMLCanvasElement).setPointerCapture(e.pointerId);
    dragRef.current = {
      active: true,
      moved: false,
      startX: e.clientX,
      startY: e.clientY,
      camStartX: camRef.current.targetX,
      camStartY: camRef.current.targetY,
    };
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;

    if (dragRef.current.active) {
      const dx = e.clientX - dragRef.current.startX;
      const dy = e.clientY - dragRef.current.startY;
      if (Math.hypot(dx, dy) > 4) {
        dragRef.current.moved = true;
      }
      camRef.current.targetX = dragRef.current.camStartX - dx / camRef.current.zoom;
      camRef.current.targetY = dragRef.current.camStartY - dy / camRef.current.zoom;
      setHoveredNode(null);
      hoveredIdRef.current = null;
      requestDrawRef.current();
      return;
    }

    const hit = hitTest(sx, sy);
    hoveredIdRef.current = hit?.node.id ?? null;
    requestDrawRef.current();
    if (hit) {
      const pos = worldToScreen(hit.x, hit.y);
      setHoveredNode({ wn: hit, sx: pos.sx, sy: pos.sy });
    } else {
      setHoveredNode(null);
    }
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const wasMoved = dragRef.current.moved;
    dragRef.current.active = false;
    dragRef.current.moved = false;
    if (!wasMoved) {
      const rect = e.currentTarget.getBoundingClientRect();
      const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top);
      if (hit) {
        focusNode(hit);
      } else {
        setPinnedId(null);
        requestDrawRef.current();
      }
    }
  };

  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.18 : 0.85;
      camRef.current.targetZoom = Math.max(0.45, Math.min(3.2, camRef.current.targetZoom * factor));
      requestDrawRef.current();
    } else if (!wheelHintShownRef.current) {
      wheelHintShownRef.current = true;
      setWheelHintVisible(true);
      window.setTimeout(() => setWheelHintVisible(false), 2600);
    }
  };

  const handleMinimapInteract = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const { minX, maxX, minY, maxY } = world.bounds;
    const wx = minX + ((mx - 8) / (rect.width - 16)) * (maxX - minX);
    const wy = minY + ((my - 8) / (rect.height - 16)) * (maxY - minY);
    flyTo(wx, wy, camRef.current.targetZoom);
  };

  const pinnedWn = pinnedId ? world.byId.get(pinnedId) ?? null : null;
  const pinnedNeighbors = useMemo(() => {
    if (!pinnedWn) return [];
    const list: Array<{ wn: WorldNode; relation: string }> = [];
    for (const e of edges) {
      if (e.source === pinnedWn.node.id) {
        const other = world.byId.get(e.target);
        if (other) list.push({ wn: other, relation: e.relation });
      } else if (e.target === pinnedWn.node.id) {
        const other = world.byId.get(e.source);
        if (other) list.push({ wn: other, relation: e.relation });
      }
    }
    return list;
  }, [edges, pinnedWn, world.byId]);

  const activeClusterObj = world.clusters.find((c) => c.key === activeClusterKey) ?? null;

  const openNodePrimaryAction = (wn: WorldNode) => {
    const meta = wn.node.meta || {};
    if (wn.kind === 'verified' || wn.kind === 'review' || wn.kind === 'cleared' || wn.kind === 'disputed') {
      const fid = (meta.finding_id ?? wn.node.id.replace(/^finding:/, '')) as string | number;
      onSelectFinding(fid);
    } else if (wn.kind === 'mission') {
      const slug = String(meta.slug || wn.node.id.replace(/^mission:/, ''));
      navigate(`/projects/${encodeURIComponent(slug)}`);
    } else if (wn.kind === 'source' && (meta.base_url || meta.url)) {
      const safeUrl = safeExternalUrl(String(meta.base_url || meta.url));
      if (safeUrl) {
        window.open(safeUrl, '_blank', 'noopener,noreferrer');
      }
    }
  };

  const stepPinned = (dir: -1 | 1) => {
    if (!pinnedWn || world.worldNodes.length === 0) return;
    const idx = world.worldNodes.findIndex((w) => w.node.id === pinnedWn.node.id);
    const next = world.worldNodes[(idx + dir + world.worldNodes.length) % world.worldNodes.length];
    if (next) focusNode(next);
  };

  return (
    <section
      className="map"
      id="map"
      ref={wrapRef}
      aria-label="Interactive Dark Observatory of Indian public-spending audit topology"
    >
      <canvas
        ref={canvasRef}
        className="map-canvas"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={() => {
          dragRef.current.active = false;
          hoveredIdRef.current = null;
          setHoveredNode(null);
        }}
        onWheel={handleWheel}
      />

      {loading && nodes.length === 0 && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--obs-muted)',
            fontFamily: 'var(--font-mono)',
            fontSize: 13,
            pointerEvents: 'none',
          }}
        >
          Mapping Indian civic-audit observatory topology…
        </div>
      )}

      {/* Top-Left Editorial Title + Breadcrumb */}
      <div className="map-head">
        <div className="map-head-inner">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <img src="/logo-dark.svg" alt="" width={64} height={64} />
            <span className="kicker" style={{ color: 'var(--obs-mist)' }}>
              Observatory map
            </span>
          </div>
          <h2>
            {verifiedCount > 0 || agentsActive > 0
              ? `${verifiedCount} peer-verified lead${verifiedCount === 1 ? '' : 's'} and ${agentsActive} agent${agentsActive === 1 ? '' : 's'} auditing across ${missionsCount} ${missionsCount === 1 ? 'project' : 'projects'}.`
              : `${missionsCount} ${missionsCount === 1 ? 'project' : 'projects'} and ${nodes.length} official datasets mapped.`}
          </h2>
          <div className="map-head-sub">
            <span className="live-pulse" style={{ color: 'var(--green-on-dark)' }}>
              <span className="live-dot sm" />
              <span>{nodes.length} mapped nodes, {edges.length} provenance links</span>
            </span>
            {activeClusterObj && (
              <span className="map-crumb">
                <button type="button" onClick={() => fitAll(false)}>
                  Map
                </button>
                <span>/</span>
                <span>
                  {activeClusterObj.shortCode}, {activeClusterObj.label.slice(0, 26)}
                </span>
              </span>
            )}
          </div>

          {/* Clickable Cluster Program Pills (.map-program) stacked cleanly inside .map-head-inner */}
          {world.clusters.length > 0 && (
            <div className="map-programs" role="group" aria-label="Jump to project cluster">
              {world.clusters.map((cl) => (
                <button
                  key={cl.key}
                  type="button"
                  className={`map-program ${activeClusterKey === cl.key ? 'active' : ''}`}
                  onClick={() => focusCluster(cl)}
                >
                  <span className="map-program-dot" style={{ background: cl.tint.hex }} />
                  <span>{cl.shortCode}</span>
                  <span style={{ opacity: 0.7 }}>{cl.totalCount}</span>
                </button>
              ))}
            </div>
          )}

          {/* Operable Mobile Mission Selector (shown <= 760px when desktop cluster pills & minimap hide) */}
          {world.clusters.length > 0 && (
            <div className="map-mobile-missions">
              <label htmlFor="map-mobile-cluster-select" className="map-mobile-label">
                Jump to audit mission:
              </label>
              <select
                id="map-mobile-cluster-select"
                className="map-mobile-select"
                value={activeClusterKey || ''}
                onChange={(e) => {
                  const key = e.target.value;
                  if (!key) {
                    fitAll(false);
                    return;
                  }
                  const found = world.clusters.find((c) => c.key === key);
                  if (found) focusCluster(found);
                }}
              >
                <option value="">All mapped projects ({world.clusters.length})</option>
                {world.clusters.map((cl) => (
                  <option key={cl.key} value={cl.key}>
                    {cl.shortCode}, {cl.label} ({cl.totalCount} nodes)
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>

      {/* Top-Right `/` Accessible Combobox Search */}
      <div
        className="map-search"
        ref={searchWrapRef}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
            setSearchFocused(false);
          }
        }}
      >
        <div className="map-search-box">
          <Search size={14} className="map-search-icon" aria-hidden="true" />
          <input
            ref={searchInputRef}
            type="text"
            role="combobox"
            aria-expanded={searchFocused && searchMatches.length > 0}
            aria-controls="map-search-listbox"
            aria-autocomplete="list"
            aria-activedescendant={
              searchFocused && searchMatches[activeMatchIdx]
                ? `map-search-opt-${activeMatchIdx}`
                : undefined
            }
            className="map-search-input"
            placeholder="Find ministry, project, finding, or source…"
            value={search}
            onFocus={() => {
              setSearchFocused(true);
              setActiveMatchIdx(0);
            }}
            onChange={(e) => {
              setSearch(e.target.value);
              setSearchFocused(true);
              setActiveMatchIdx(0);
            }}
            onKeyDown={(e) => {
              if (!searchFocused && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
                setSearchFocused(true);
                setActiveMatchIdx(0);
                e.preventDefault();
                return;
              }
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                if (searchMatches.length > 0) {
                  setActiveMatchIdx((idx) => (idx + 1) % searchMatches.length);
                }
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                if (searchMatches.length > 0) {
                  setActiveMatchIdx((idx) => (idx - 1 + searchMatches.length) % searchMatches.length);
                }
              } else if (e.key === 'Home' && searchMatches.length > 0) {
                e.preventDefault();
                setActiveMatchIdx(0);
              } else if (e.key === 'End' && searchMatches.length > 0) {
                e.preventDefault();
                setActiveMatchIdx(searchMatches.length - 1);
              } else if (e.key === 'Enter') {
                const chosen = searchMatches[activeMatchIdx] ?? searchMatches[0];
                if (chosen) {
                  e.preventDefault();
                  focusNode(chosen);
                  setSearchFocused(false);
                }
              } else if (e.key === 'Escape') {
                e.preventDefault();
                setSearchFocused(false);
              }
            }}
            aria-label="Search observatory nodes"
          />
          {search ? (
            <button
              type="button"
              onClick={() => {
                setSearch('');
                setActiveMatchIdx(0);
                searchInputRef.current?.focus();
              }}
              style={{
                position: 'absolute',
                right: 9,
                background: 'none',
                border: 'none',
                color: 'var(--obs-mist)',
                padding: 2,
              }}
              aria-label="Clear search"
            >
              <X size={13} />
            </button>
          ) : (
            <span className="map-search-kbd" aria-hidden="true">/</span>
          )}
        </div>

        {searchFocused && searchMatches.length > 0 && (
          <div
            id="map-search-listbox"
            className="map-search-results"
            role="listbox"
            aria-label="Matching observatory nodes"
          >
            {searchMatches.map((wn, idx) => {
              const isHighlighted = idx === activeMatchIdx;
              return (
                <button
                  key={wn.node.id}
                  id={`map-search-opt-${idx}`}
                  type="button"
                  role="option"
                  aria-selected={isHighlighted}
                  className={`map-search-item ${isHighlighted ? 'active' : ''}`}
                  onMouseEnter={() => setActiveMatchIdx(idx)}
                  onClick={() => {
                    focusNode(wn);
                    setSearchFocused(false);
                  }}
                >
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {wn.node.label}
                  </span>
                  <span
                    className="font-mono"
                    style={{
                      fontSize: 10.5,
                      textTransform: 'uppercase',
                      color:
                        wn.kind === 'verified'
                          ? '#0F7306'
                          : wn.kind === 'mission'
                            ? '#8A4200'
                            : '#45351F',
                      flexShrink: 0,
                    }}
                  >
                    {wn.node.status || wn.node.type}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Dark Hover Tooltip (.map-card) */}
      {hoveredNode && hoveredNode.wn.node.id !== pinnedId && (
        <div
          className="map-card"
          style={{
            left: Math.min(
              (wrapRef.current?.clientWidth ?? 1200) - 290,
              Math.max(16, hoveredNode.sx + 16),
            ),
            top: Math.max(16, hoveredNode.sy - 28),
          }}
        >
          <div className="map-card-meta">
            <span>{hoveredNode.wn.node.type}</span>
            {hoveredNode.wn.node.status && <span>{hoveredNode.wn.node.status.replace(/_/g, ' ')}</span>}
          </div>
          <div className="map-card-title">{hoveredNode.wn.node.label}</div>
          {hoveredNode.wn.node.value_inr_cr != null && hoveredNode.wn.node.value_inr_cr > 0 && (
            <div className="font-mono tabular" style={{ fontSize: 11, color: '#0F7306', marginTop: 4 }}>
              {formatInrCr(hoveredNode.wn.node.value_inr_cr)}
            </div>
          )}
        </div>
      )}

      {/* Floating Warm-Paper Pinned Inspector Sheet (.map-sheet) */}
      {pinnedWn && (
        <aside className="map-sheet" aria-label="Pinned observatory node details">
          <div className="map-sheet-top">
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              {pinnedWn.node.status ? (
                <span className={statusMeta(pinnedWn.node.status).badgeClass}>
                  {statusMeta(pinnedWn.node.status).label}
                </span>
              ) : (
                <span className="tag-mono" style={{ textTransform: 'uppercase' }}>
                  {pinnedWn.node.type}
                </span>
              )}
              {Boolean(pinnedWn.node.meta?.project_code || pinnedWn.node.meta?.methodology_code) && (
                <span className="tag-mono tag-method">
                  {projectCodeFor({
                    project_code: pinnedWn.node.meta?.project_code
                      ? String(pinnedWn.node.meta.project_code)
                      : undefined,
                    slug: pinnedWn.node.meta?.slug
                      ? String(pinnedWn.node.meta.slug)
                      : pinnedWn.node.id.replace(/^mission:/, ''),
                    methodology_code: pinnedWn.node.meta?.methodology_code
                      ? String(pinnedWn.node.meta.methodology_code)
                      : undefined,
                  })}
                </span>
              )}
              {pinnedWn.isCommunity && (
                <span className="tag-provider">Community Node</span>
              )}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <button
                type="button"
                className="btn btn-ghost btn-xs"
                onClick={() => stepPinned(-1)}
                aria-label="Previous node"
              >
                <ChevronLeft size={13} />
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs"
                onClick={() => stepPinned(1)}
                aria-label="Next node"
              >
                <ChevronRight size={13} />
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-xs"
                onClick={() => setPinnedId(null)}
                aria-label="Close inspector"
              >
                <X size={14} />
              </button>
            </div>
          </div>

          <h3
            className={`map-sheet-title ${
              isUnverifiedStatus(pinnedWn.node.status) ? 'unverified' : ''
            }`}
          >
            {pinnedWn.node.label}
          </h3>

          <div className="font-mono" style={{ fontSize: 11.5, color: 'var(--ink-3)', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <span>{pillarLabel(pinnedWn.pillar)}</span>
            {Boolean(pinnedWn.node.meta?.entity_name) && (
              <span>{String(pinnedWn.node.meta?.entity_name)}</span>
            )}
            {Boolean(pinnedWn.node.meta?.authority) && (
              <span>{String(pinnedWn.node.meta?.authority)}</span>
            )}
            {Boolean(pinnedWn.node.meta?.jurisdiction) && (
              <span>{String(pinnedWn.node.meta?.jurisdiction)}</span>
            )}
            {Boolean(pinnedWn.node.meta?.submitted_by) && (
              <span>@{String(pinnedWn.node.meta?.submitted_by)}</span>
            )}
          </div>

          {Boolean(pinnedWn.node.meta?.description) && (
            <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.5, color: 'var(--ink-2)' }}>
              {String(pinnedWn.node.meta?.description)}
            </p>
          )}

          {pinnedWn.node.value_inr_cr != null && pinnedWn.node.value_inr_cr > 0 && (
            <div
              style={{
                padding: '10px 12px',
                background: 'var(--raised)',
                border: '1px solid var(--rule)',
                borderRadius: 'var(--r-8)',
                display: 'flex',
                alignItems: 'baseline',
                justifyContent: 'space-between',
                gap: 8,
              }}
            >
              <span className="kicker">Quantified Exposure</span>
              <span className="font-mono tabular" style={{ fontSize: 18, fontWeight: 600, color: 'var(--ink)' }}>
                {formatInrCr(pinnedWn.node.value_inr_cr)}
              </span>
            </div>
          )}

          {pinnedNeighbors.length > 0 && (
            <div>
              <div className="kicker" style={{ marginBottom: 7 }}>
                Connected Provenance ({pinnedNeighbors.length})
              </div>
              <div className="map-sheet-neighbors">
                {pinnedNeighbors.slice(0, 6).map(({ wn, relation }, i) => (
                  <button
                    key={`${wn.node.id}-${i}`}
                    type="button"
                    className="map-neighbor-pill"
                    onClick={() => focusNode(wn)}
                    title={`${relation}: ${wn.node.label}`}
                  >
                    {wn.node.label.length > 30 ? `${wn.node.label.slice(0, 29)}…` : wn.node.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div style={{ marginTop: 'auto', paddingTop: 6 }}>
            {(pinnedWn.kind === 'verified' ||
              pinnedWn.kind === 'review' ||
              pinnedWn.kind === 'cleared' ||
              pinnedWn.kind === 'disputed') && (
              <button
                type="button"
                className="btn btn-primary"
                style={{ width: '100%' }}
                onClick={() => openNodePrimaryAction(pinnedWn)}
              >
                <span>Open Evidence Dossier &amp; RTI Draft</span>
                <ArrowUpRight size={15} />
              </button>
            )}
            {pinnedWn.kind === 'mission' && (
              <button
                type="button"
                className="btn btn-primary"
                style={{ width: '100%' }}
                onClick={() => openNodePrimaryAction(pinnedWn)}
              >
                <span>Open project</span>
                <ArrowUpRight size={15} />
              </button>
            )}
            {pinnedWn.kind === 'source' &&
              Boolean(
                safeExternalUrl(
                  pinnedWn.node.meta?.base_url
                    ? String(pinnedWn.node.meta.base_url)
                    : pinnedWn.node.meta?.url
                      ? String(pinnedWn.node.meta.url)
                      : null,
                ),
              ) && (
                <button
                  type="button"
                  className="btn btn-secondary"
                  style={{ width: '100%' }}
                  onClick={() => openNodePrimaryAction(pinnedWn)}
                >
                  <span>Visit Official .gov.in Portal</span>
                  <ExternalLink size={14} />
                </button>
              )}
          </div>
        </aside>
      )}

      {/* Bottom-Left Interactive Minimap + Epistemic Legend */}
      <div className="map-bottom-left">
        <div className="map-minimap-wrap" title="Click to pan observatory viewport">
          <canvas
            ref={minimapRef}
            style={{ width: '150px', height: '94px', display: 'block' }}
            onClick={handleMinimapInteract}
          />
        </div>

        <div className="map-legend">
          <span className="map-leg-item">
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: '#FFFFFF',
                boxShadow: '0 0 7px rgba(255,255,255,0.8)',
              }}
            />
            <span>verified</span>
          </span>
          <span className="map-leg-item" style={{ fontStyle: 'italic' }}>
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                border: '1.25px dashed #3A2E1E',
              }}
            />
            <span>in review</span>
          </span>
          <span className="map-leg-item">
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#1B1A17' }} />
            <span>agent comet</span>
          </span>
          <span className="map-leg-item">
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                border: '1.4px solid #8A1C0C',
              }}
            />
            <span>disputed ×</span>
          </span>
          <span className="map-leg-item">
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                border: '1.4px solid #0F7306',
              }}
            />
            <span>cleared −</span>
          </span>
          <span className="map-leg-item">
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: '50%',
                border: '2px solid #1B1A17',
              }}
            />
            <span>community node</span>
          </span>
        </div>
      </div>

      {/* Bottom-Right Zoom Controls */}
      <div className="map-zoom" aria-label="Observatory zoom controls">
        <button
          type="button"
          onClick={() => {
            camRef.current.targetZoom = Math.min(3.2, camRef.current.targetZoom * 1.28);
            requestDrawRef.current();
          }}
          aria-label="Zoom in"
          title="Zoom in"
        >
          <Plus size={16} />
        </button>
        <button
          type="button"
          onClick={() => {
            camRef.current.targetZoom = Math.max(0.45, camRef.current.targetZoom / 1.28);
            requestDrawRef.current();
          }}
          aria-label="Zoom out"
          title="Zoom out"
        >
          <Minus size={16} />
        </button>
        <button
          type="button"
          onClick={() => {
            setPinnedId(null);
            fitAll(false);
            requestDrawRef.current();
          }}
          aria-label="Reset camera"
          title="Reset camera (Esc)"
        >
          <RotateCcw size={14} />
        </button>
      </div>

      {wheelHintVisible && (
        <div className="map-wheel-hint">
          Hold Ctrl / ⌘ + scroll to zoom, drag to pan, press / to search
        </div>
      )}
    </section>
  );
}
