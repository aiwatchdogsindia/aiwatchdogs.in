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
    footerSub: 'Join community server',
    href: 'https://discord.gg/8K33UYGWY4',
    tagline: 'Coordinate contributor agents, peer-review leads & discuss RTI drafts.',
    cta: 'Join Discord',
    ariaLabel: 'Join the AI Watchdogs India Discord server',
  },
  {
    id: 'github',
    name: 'GitHub',
    shortName: 'GitHub',
    handle: 'piyushbodaa/aiwatchdogs.in',
    footerSub: 'piyushbodaa/aiwatchdogs.in',
    href: 'https://github.com/piyushbodaa/aiwatchdogs.in',
    tagline: 'Inspect MIT-licensed M1–M8 engines, coordinator hub & web source.',
    cta: 'View repository',
    ariaLabel: 'View AI Watchdogs India public repository on GitHub (piyushbodaa/aiwatchdogs.in)',
  },
] as const;
