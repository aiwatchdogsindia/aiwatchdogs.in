import { ArrowUpRight, ExternalLink, RotateCcw, Search, Sparkles } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from '../router';
import type { GraphData, GraphNode } from '../types';
import { formatInrCr, pillarLabel, statusMeta } from '../utils';

interface ObservatoryMapProps {
  graph: GraphData | null;
  loading?: boolean;
  onSelectFinding: (id: string | number) => void;
}

type NodeFilter = 'all' | 'ministry' | 'mission' | 'finding' | 'source';

function canonicalColumn(type: string): number {
  const t = type.toLowerCase();
  if (t.includes('ministry') || t.includes('jurisdiction') || t.includes('entity') || t.includes('authority')) {
    return 0;
  }
  if (t.includes('mission')) return 1;
  if (t.includes('finding') || t.includes('lead')) return 2;
  return 3; // source / portal
}

function canonicalFilterGroup(type: string): NodeFilter {
  const col = canonicalColumn(type);
  if (col === 0) return 'ministry';
  if (col === 1) return 'mission';
  if (col === 2) return 'finding';
  return 'source';
}

function nodeVisual(node: GraphNode): { fill: string; glow: string; radius: number; dashed: boolean } {
  const grp = canonicalFilterGroup(node.type);
  const st = (node.status || '').toLowerCase();

  if (grp === 'ministry') {
    return { fill: '#F3E9D2', glow: 'rgba(243, 233, 210, 0.45)', radius: 8.5, dashed: false };
  }
  if (grp === 'mission') {
    return { fill: '#F2B84B', glow: 'rgba(242, 184, 75, 0.55)', radius: 10, dashed: false };
  }
  if (grp === 'source') {
    return { fill: '#93C5FD', glow: 'rgba(147, 197, 253, 0.5)', radius: 7.5, dashed: false };
  }
  // finding
  if (st === 'verified') {
    return { fill: '#4ADE80', glow: 'rgba(74, 222, 128, 0.65)', radius: 9.5, dashed: false };
  }
  if (st === 'cleared' || st === 'resolved_explained') {
    return { fill: '#94A3B8', glow: 'rgba(148, 163, 184, 0.4)', radius: 8, dashed: false };
  }
  if (st === 'disputed' || st === 'retracted_false_positive') {
    return { fill: '#F87171', glow: 'rgba(248, 113, 113, 0.55)', radius: 8.5, dashed: false };
  }
  return { fill: '#F2B84B', glow: 'rgba(242, 184, 75, 0.45)', radius: 8.5, dashed: true };
}

function truncateLabel(label: string, max = 26): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

export function ObservatoryMap({ graph, loading, onSelectFinding }: ObservatoryMapProps) {
  const { navigate } = useRouter();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<NodeFilter>('all');
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  // `/` keyboard shortcut focuses Observatory search filter
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== '/') return;
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (searchRef.current) {
        e.preventDefault();
        searchRef.current.focus();
        searchRef.current.select();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];

  const filteredIds = useMemo(() => {
    const q = search.trim().toLowerCase();
    const matched = new Set<string>();
    for (const n of nodes) {
      const grp = canonicalFilterGroup(n.type);
      const typeOk = filter === 'all' || grp === filter;
      const metaText = n.meta ? JSON.stringify(n.meta).toLowerCase() : '';
      const queryOk =
        !q ||
        n.label.toLowerCase().includes(q) ||
        (n.pillar || '').toLowerCase().includes(q) ||
        (n.status || '').toLowerCase().includes(q) ||
        metaText.includes(q);
      if (typeOk && queryOk) {
        matched.add(n.id);
      }
    }
    return matched;
  }, [nodes, search, filter]);

  // Build deterministic 4-column layered coordinates with collision-free right-hand label lanes
  const layout = useMemo(() => {
    const W = 1080;
    const cols: GraphNode[][] = [[], [], [], []];
    for (const n of nodes) {
      cols[canonicalColumn(n.type)].push(n);
    }
    // Sort each column by pillar then label so related chains cluster cleanly
    for (const col of cols) {
      col.sort((a, b) =>
        `${a.pillar || ''}-${a.label}`.localeCompare(`${b.pillar || ''}-${b.label}`),
      );
    }

    const maxColLen = Math.max(1, ...cols.map((c) => c.length));
    const H = Math.max(480, Math.min(760, maxColLen * 44 + 72));
    const colX = [28, 296, 566, 836];

    const pos = new Map<string, { x: number; y: number; node: GraphNode; col: number }>();
    cols.forEach((colNodes, cIdx) => {
      const count = colNodes.length;
      const topPad = 42;
      const usableH = H - topPad * 2;
      colNodes.forEach((node, i) => {
        const y = count === 1 ? H / 2 : topPad + (usableH * i) / (count - 1);
        pos.set(node.id, { x: colX[cIdx], y, node, col: cIdx });
      });
    });

    return { W, H, pos };
  }, [nodes]);

  const activeFocusId = hoveredId || selectedId;

  const connectedToFocus = useMemo(() => {
    if (!activeFocusId) return new Set<string>();
    const set = new Set<string>([activeFocusId]);
    for (const e of edges) {
      if (e.source === activeFocusId) set.add(e.target);
      if (e.target === activeFocusId) set.add(e.source);
    }
    return set;
  }, [activeFocusId, edges]);

  const selectedNode = useMemo(() => {
    if (!selectedId) return nodes.find((n) => canonicalFilterGroup(n.type) === 'finding') || nodes[0] || null;
    return nodes.find((n) => n.id === selectedId) || null;
  }, [nodes, selectedId]);

  const selectedEdges = useMemo(() => {
    if (!selectedNode) return [];
    return edges
      .filter((e) => e.source === selectedNode.id || e.target === selectedNode.id)
      .map((e) => {
        const otherId = e.source === selectedNode.id ? e.target : e.source;
        const other = nodes.find((n) => n.id === otherId);
        return { relation: e.relation, other };
      })
      .filter((item) => Boolean(item.other));
  }, [edges, nodes, selectedNode]);

  const handleInspectAction = (node: GraphNode) => {
    const grp = canonicalFilterGroup(node.type);
    const meta = node.meta || {};
    if (grp === 'finding') {
      const fid = (meta.finding_id ?? node.id.replace(/^finding:/, '')) as string | number;
      onSelectFinding(fid);
    } else if (grp === 'mission') {
      const slug = String(meta.slug || node.id.replace(/^mission:/, ''));
      navigate(`/missions/${encodeURIComponent(slug)}`);
    } else if (grp === 'source' && (meta.base_url || meta.url)) {
      window.open(String(meta.base_url || meta.url), '_blank', 'noopener,noreferrer');
    }
  };

  return (
    <div className="observatory-shell">
      <div className="observatory-toolbar">
        <div className="obs-search-wrap">
          <Search size={14} className="obs-search-icon" />
          <input
            ref={searchRef}
            type="text"
            className="obs-search-input"
            placeholder="Filter ministries, missions, findings, .gov.in portals…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Filter observatory network graph"
          />
          {search ? (
            <button
              type="button"
              onClick={() => setSearch('')}
              style={{
                position: 'absolute',
                right: 7,
                background: 'none',
                border: 'none',
                color: '#9AA4B2',
                padding: 2,
              }}
              aria-label="Clear search"
            >
              <RotateCcw size={12} />
            </button>
          ) : (
            <span className="obs-kbd">/</span>
          )}
        </div>

        <div className="obs-filters" role="group" aria-label="Filter by node type">
          {(
            [
              ['all', 'All Nodes', '#F3E9D2'],
              ['ministry', 'Ministries', '#F3E9D2'],
              ['mission', 'Missions', '#F2B84B'],
              ['finding', 'Findings', '#4ADE80'],
              ['source', '.gov.in Sources', '#93C5FD'],
            ] as Array<[NodeFilter, string, string]>
          ).map(([key, label, dot]) => (
            <button
              key={key}
              type="button"
              className={`obs-pill ${filter === key ? 'active' : ''}`}
              onClick={() => setFilter(key)}
            >
              <span className="obs-dot" style={{ background: dot }} />
              <span>{label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="obs-body">
        <div className="obs-canvas-wrap">
          <div className="obs-col-headers">
            <span>I · Ministries &amp; Jurisdictions</span>
            <span>II · Audit Missions (M1–M8)</span>
            <span>III · Audit Leads &amp; Findings</span>
            <span>IV · Official .gov.in Sources</span>
          </div>

          {loading ? (
            <div style={{ padding: 64, textAlign: 'center', color: '#9AA4B2', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
              Mapping public-spending knowledge graph…
            </div>
          ) : nodes.length === 0 ? (
            <div style={{ padding: 64, textAlign: 'center', color: '#9AA4B2', fontSize: 13 }}>
              No graph nodes available yet. Connect the Hub API to render the live civic-audit topology.
            </div>
          ) : (
            <svg
              viewBox={`0 0 ${layout.W} ${layout.H}`}
              width="100%"
              style={{ display: 'block', minHeight: 460, maxHeight: 540 }}
              role="img"
              aria-label="Interactive civic audit observatory graph linking Indian Ministries, Missions, Findings, and Official Government Sources"
            >
              <defs>
                <filter id="obs-glow-emerald" x="-60%" y="-60%" width="220%" height="220%">
                  <feGaussianBlur stdDeviation="4" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
                <filter id="obs-glow-amber" x="-60%" y="-60%" width="220%" height="220%">
                  <feGaussianBlur stdDeviation="3.5" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>

              {/* Curved edges (non-interactive so node clicks never fall through) */}
              <g pointerEvents="none">
                {edges.map((edge, idx) => {
                  const s = layout.pos.get(edge.source);
                  const t = layout.pos.get(edge.target);
                  if (!s || !t) return null;
                  const dx = (t.x - s.x) * 0.45;
                  const d = `M ${s.x} ${s.y} C ${s.x + dx} ${s.y}, ${t.x - dx} ${t.y}, ${t.x} ${t.y}`;
                  const isHighlighted =
                    activeFocusId &&
                    (edge.source === activeFocusId || edge.target === activeFocusId);
                  const isVisible =
                    filteredIds.has(edge.source) || filteredIds.has(edge.target);
                  const opacity = activeFocusId
                    ? isHighlighted
                      ? 0.9
                      : 0.08
                    : isVisible
                      ? 0.28
                      : 0.05;
                  return (
                    <path
                      key={`${edge.source}-${edge.target}-${idx}`}
                      d={d}
                      fill="none"
                      stroke={isHighlighted ? '#F2B84B' : '#64748B'}
                      strokeWidth={isHighlighted ? 1.8 : 1}
                      opacity={opacity}
                      pointerEvents="none"
                      style={{ transition: 'opacity 0.18s ease, stroke 0.18s ease' }}
                    />
                  );
                })}
              </g>

              {/* Glowing Nodes */}
              <g>
                {nodes.map((node) => {
                  const pt = layout.pos.get(node.id);
                  if (!pt) return null;
                  const vis = nodeVisual(node);
                  const inFilter = filteredIds.has(node.id);
                  const inFocus = !activeFocusId || connectedToFocus.has(node.id);
                  const isSelected = selectedNode?.id === node.id;
                  const isHovered = hoveredId === node.id;
                  const opacity = !inFilter ? 0.14 : inFocus ? 1 : 0.22;
                  const labelX = pt.x + 14;
                  const maxChars = pt.col === 3 ? 21 : 23;

                  return (
                    <g
                      key={node.id}
                      data-node-id={node.id}
                      style={{ cursor: 'pointer', transition: 'opacity 0.18s ease' }}
                      opacity={opacity}
                      onMouseEnter={() => setHoveredId(node.id)}
                      onMouseLeave={() => setHoveredId(null)}
                      onClick={() => setSelectedId(node.id)}
                    >
                      {/* Generous transparent hit target covering circle + label */}
                      <rect
                        x={pt.x - 12}
                        y={pt.y - 14}
                        width={230}
                        height={28}
                        fill="transparent"
                      />

                      {/* Atmospheric halo */}
                      {(isSelected || isHovered || node.status === 'verified') && (
                        <circle
                          cx={pt.x}
                          cy={pt.y}
                          r={vis.radius + (isSelected || isHovered ? 7 : 4.5)}
                          fill={vis.glow}
                          opacity={isSelected || isHovered ? 0.45 : 0.24}
                        />
                      )}

                      {/* Core node */}
                      <circle
                        cx={pt.x}
                        cy={pt.y}
                        r={isSelected || isHovered ? vis.radius + 1.5 : vis.radius}
                        fill={vis.dashed ? '#11151C' : vis.fill}
                        stroke={isSelected ? '#FFFFFF' : vis.fill}
                        strokeWidth={vis.dashed ? 1.8 : isSelected ? 2 : 1.2}
                        strokeDasharray={vis.dashed ? '3 2' : undefined}
                        filter={
                          node.status === 'verified'
                            ? 'url(#obs-glow-emerald)'
                            : canonicalFilterGroup(node.type) === 'mission'
                              ? 'url(#obs-glow-amber)'
                              : undefined
                        }
                      />

                      <text
                        x={labelX}
                        y={pt.y + 4}
                        textAnchor="start"
                        fill={isSelected || isHovered ? '#FFFFFF' : '#D5D9E2'}
                        fontSize={isSelected || isHovered ? '11.5' : '10.5'}
                        fontFamily="var(--font-mono)"
                        fontWeight={isSelected || isHovered ? '600' : '400'}
                        style={{ userSelect: 'none' }}
                      >
                        {truncateLabel(node.label, maxChars)}
                      </text>
                    </g>
                  );
                })}
              </g>
            </svg>
          )}
        </div>

        {/* Click-to-Inspect Side Panel */}
        <aside className="obs-inspector" aria-label="Selected observatory node details">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span
              className="font-mono"
              style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#F2B84B' }}
            >
              <Sparkles size={11} style={{ display: 'inline', marginRight: 5, verticalAlign: '-1px' }} />
              Node Inspector
            </span>
            <span className="font-mono tabular" style={{ fontSize: 11, color: '#9AA4B2' }}>
              {filteredIds.size}/{nodes.length} shown
            </span>
          </div>

          {selectedNode ? (
            <>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                  <span
                    className="font-mono"
                    style={{
                      fontSize: 10.5,
                      padding: '2px 7px',
                      borderRadius: 2,
                      background: '#1B2230',
                      border: '1px solid #2D3748',
                      color: '#CBD5E1',
                      textTransform: 'uppercase',
                    }}
                  >
                    {selectedNode.type}
                  </span>
                  {selectedNode.status && (
                    <span className={statusMeta(selectedNode.status).badgeClass}>
                      {statusMeta(selectedNode.status).label}
                    </span>
                  )}
                </div>

                <h4
                  className="font-serif"
                  style={{ margin: '0 0 6px', fontSize: 19, lineHeight: 1.25, color: '#F3E9D2' }}
                >
                  {selectedNode.label}
                </h4>

                {selectedNode.pillar && (
                  <div style={{ fontSize: 12, color: '#9AA4B2' }}>
                    Pillar: <strong style={{ color: '#E2E8F0' }}>{pillarLabel(selectedNode.pillar)}</strong>
                  </div>
                )}
              </div>

              {selectedNode.value_inr_cr != null && selectedNode.value_inr_cr > 0 && (
                <div
                  style={{
                    padding: '10px 12px',
                    background: '#0A0C10',
                    border: '1px solid #232B38',
                    borderRadius: 4,
                  }}
                >
                  <div className="font-mono" style={{ fontSize: 10.5, color: '#9AA4B2', textTransform: 'uppercase' }}>
                    Quantified Exposure / Audited Volume
                  </div>
                  <div
                    className="font-mono tabular"
                    style={{ fontSize: 20, fontWeight: 600, color: '#4ADE80', marginTop: 2 }}
                  >
                    {formatInrCr(selectedNode.value_inr_cr)}
                  </div>
                </div>
              )}

              {selectedNode.meta && Object.keys(selectedNode.meta).length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12 }}>
                  {Object.entries(selectedNode.meta)
                    .filter(([k, v]) => v != null && v !== '' && !['slug', 'finding_id', 'url'].includes(k))
                    .slice(0, 6)
                    .map(([k, v]) => (
                      <div
                        key={k}
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          gap: 10,
                          paddingBottom: 5,
                          borderBottom: '1px solid #1E2532',
                        }}
                      >
                        <span style={{ color: '#9AA4B2', textTransform: 'capitalize' }}>
                          {k.replace(/_/g, ' ')}
                        </span>
                        <span className="font-mono tabular" style={{ color: '#E2E8F0', textAlign: 'right' }}>
                          {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                        </span>
                      </div>
                    ))}
                </div>
              )}

              {selectedEdges.length > 0 && (
                <div>
                  <div
                    className="font-mono"
                    style={{ fontSize: 10.5, color: '#9AA4B2', textTransform: 'uppercase', marginBottom: 6 }}
                  >
                    Linked Graph Edges ({selectedEdges.length})
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                    {selectedEdges.slice(0, 6).map((item, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => item.other && setSelectedId(item.other.id)}
                        style={{
                          textAlign: 'left',
                          background: '#171D27',
                          border: '1px solid #263042',
                          borderRadius: 3,
                          padding: '6px 9px',
                          color: '#E2E8F0',
                          fontSize: 11.5,
                          display: 'flex',
                          justifyContent: 'space-between',
                          gap: 8,
                        }}
                      >
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {item.other?.label}
                        </span>
                        <span className="font-mono" style={{ color: '#93C5FD', fontSize: 10.5, flexShrink: 0 }}>
                          {item.relation}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div style={{ marginTop: 'auto', paddingTop: 8 }}>
                {canonicalFilterGroup(selectedNode.type) === 'finding' && (
                  <button
                    type="button"
                    className="btn btn-emerald"
                    style={{ width: '100%' }}
                    onClick={() => handleInspectAction(selectedNode)}
                  >
                    <span>Open Evidence Dossier &amp; RTI Draft</span>
                    <ArrowUpRight size={14} />
                  </button>
                )}
                {canonicalFilterGroup(selectedNode.type) === 'mission' && (
                  <button
                    type="button"
                    className="btn btn-primary"
                    style={{ width: '100%', background: '#F2B84B', color: '#0A0C10', borderColor: '#F2B84B' }}
                    onClick={() => handleInspectAction(selectedNode)}
                  >
                    <span>Open Audit Mission Brief</span>
                    <ArrowUpRight size={14} />
                  </button>
                )}
                {canonicalFilterGroup(selectedNode.type) === 'source' &&
                  Boolean(selectedNode.meta?.base_url || selectedNode.meta?.url) && (
                  <button
                    type="button"
                    className="btn btn-secondary"
                    style={{ width: '100%', background: '#171D27', color: '#93C5FD', borderColor: '#334155' }}
                    onClick={() => handleInspectAction(selectedNode)}
                  >
                    <span>Visit Official Portal</span>
                    <ExternalLink size={14} />
                  </button>
                )}
              </div>
            </>
          ) : (
            <p style={{ fontSize: 12.5, color: '#9AA4B2', margin: 0 }}>
              Click any node in the observatory to inspect rupee exposure, statutory anchors, and provenance links.
            </p>
          )}
        </aside>
      </div>

      <div className="obs-footer">
        <div className="obs-legend">
          <span className="obs-legend-item">
            <span className="obs-dot" style={{ background: '#F3E9D2' }} />
            Ministry / Jurisdiction
          </span>
          <span className="obs-legend-item">
            <span className="obs-dot" style={{ background: '#F2B84B' }} />
            Audit Mission
          </span>
          <span className="obs-legend-item">
            <span className="obs-dot" style={{ background: '#4ADE80' }} />
            Peer-Verified Finding (Solid)
          </span>
          <span className="obs-legend-item">
            <span
              className="obs-dot"
              style={{ background: 'transparent', border: '1.5px dashed #F2B84B' }}
            />
            In Peer Review (Dashed)
          </span>
          <span className="obs-legend-item">
            <span className="obs-dot" style={{ background: '#93C5FD' }} />
            Official .gov.in Source
          </span>
        </div>
        <span className="font-mono" style={{ fontSize: 11 }}>
          Press <kbd style={{ color: '#F2B84B' }}>/</kbd> to search · Click any node to inspect
        </span>
      </div>
    </div>
  );
}
