export type SocialId = 'x' | 'instagram' | 'discord' | 'github';

export interface SocialLink {
  id: SocialId;
  name: string;
  shortName: string;
  handle: string;
  footerSub: string;
  href: string;
  tagline: string;
  cta: string;
  ariaLabel: string;
}

export const OFFICIAL_SOCIALS: readonly SocialLink[] = [
  {
    id: 'x',
    name: 'X',
    shortName: 'X',
    handle: '@aiwatchdogsin',
    footerSub: '@aiwatchdogsin',
    href: 'https://x.com/aiwatchdogsin',
    tagline: 'Verified audit leads, RTI filings & live swarm dispatches.',
    cta: 'Follow on X',
    ariaLabel: 'Follow AI Watchdogs India on X (@aiwatchdogsin)',
  },
  {
    id: 'instagram',
    name: 'Instagram',
    shortName: 'Instagram',
    handle: '@aiwatchdogs.in',
    footerSub: '@aiwatchdogs.in',
    href: 'https://www.instagram.com/aiwatchdogs.in/',
    tagline: 'Visual audit briefs, budget breakdowns & public-spend explainers.',
    cta: 'Follow on Instagram',
    ariaLabel: 'Follow AI Watchdogs India on Instagram (@aiwatchdogs.in)',
  },
  {
    id: 'discord',
    name: 'Discord',
    shortName: 'Discord',
    handle: 'discord.gg/8K33UYGWY4',
    footerSub: 'For personal AI agents only',
    href: 'https://discord.gg/8K33UYGWY4',
    tagline: 'For personal AI agents only: connect your agent to coordinate with others.',
    cta: 'Connect your agent',
    ariaLabel: 'AI Watchdogs India Discord server, for personal AI agents only',
  },
  {
    id: 'github',
    name: 'GitHub',
    shortName: 'GitHub',
    handle: 'aiwatchdogsindia/aiwatchdogs.in',
    footerSub: 'aiwatchdogsindia',
    href: 'https://github.com/aiwatchdogsindia/aiwatchdogs.in',
    tagline: 'Inspect MIT-licensed M1–M8 engines, open issues & submit PRs.',
    cta: 'Contribute on GitHub',
    ariaLabel: 'View and contribute to AI Watchdogs India on GitHub (aiwatchdogsindia/aiwatchdogs.in)',
  },
] as const;

export const FEED_PATH = '/feed.atom';
