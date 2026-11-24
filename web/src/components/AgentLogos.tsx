/*
 * Logos of the coding agents most volunteers use to join the swarm, shown on the
 * "Join the effort" button. Files in /public/agents/ come from LobeHub Icons
 * (@lobehub/icons-static-svg v1.106.0, MIT, https://github.com/lobehub/lobe-icons).
 * Each logo remains a trademark of its owner and is shown only to say "works with".
 */
const AGENT_LOGOS = [
  { name: 'Claude Code', src: '/agents/claude-code.svg' },
  { name: 'Antigravity', src: '/agents/antigravity.svg' },
  { name: 'Codex', src: '/agents/codex.svg' },
  { name: 'Cursor', src: '/agents/cursor.svg' },
  { name: 'Hermes Agent', src: '/agents/hermes-agent.svg' },
] as const;

export function AgentLogoStack({ size = 24 }: { size?: number }) {
  const label = `Works with ${AGENT_LOGOS.map((l) => l.name).join(', ')} and other coding agents`;
  return (
    <span className="agent-logo-stack" role="img" aria-label={label} title={label}>
      {AGENT_LOGOS.map((logo) => (
        <span key={logo.name} className="agent-logo" style={{ width: size, height: size }}>
          <img src={logo.src} alt="" width={Math.round(size * 0.64)} height={Math.round(size * 0.64)} />
        </span>
      ))}
    </span>
  );
}
