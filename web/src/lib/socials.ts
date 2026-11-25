export type SocialId = 'x' | 'instagram' | 'telegram' | 'discord' | 'github';

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
    id: 'telegram',
    name: 'Telegram',
    shortName: 'Telegram',
    handle: 't.me/aiwatchdogsin',
    footerSub: '@aiwatchdogsin',
    href: 'https://t.me/aiwatchdogsin',
    tagline: 'Instant verified audit alerts, new projects & RTI updates.',
    cta: 'Join Telegram channel',
    ariaLabel: 'Join the AI Watchdogs India Telegram channel (@aiwatchdogsin)',
  },
  {
    id: 'discord',
    name: 'Discord',
    shortName: 'Discord',
    handle: 'discord.gg/8K33UYGWY4',
    footerSub: 'Join the AI Watchdogs community',
    href: 'https://discord.gg/8K33UYGWY4',
    tagline: 'Join the AI Watchdogs community: discuss audits, datasets, and agent runs.',
    cta: 'Join Discord',
    ariaLabel: 'Join the AI Watchdogs community on Discord',
  },
  {
    id: 'github',
    name: 'GitHub',
    shortName: 'GitHub',
    handle: 'aiwatchdogsindia/aiwatchdogs.in',
    footerSub: 'aiwatchdogsindia',
    href: 'https://github.com/aiwatchdogsindia/aiwatchdogs.in',
    tagline: 'Inspect the MIT-licensed code, open issues & submit PRs.',
    cta: 'Contribute on GitHub',
    ariaLabel: 'View and contribute to AI Watchdogs India on GitHub (aiwatchdogsindia/aiwatchdogs.in)',
  },
] as const;

export const FEED_PATH = '/feed.atom';
