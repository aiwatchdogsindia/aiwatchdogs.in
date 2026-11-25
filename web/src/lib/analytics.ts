/*
 * Google Analytics 4, loaded only when VITE_GA_MEASUREMENT_ID is set at build time
 * (Vercel → Project → Settings → Environment Variables, e.g. G-XXXXXXXXXX).
 * Page views (including in-app navigations) are counted by GA4 Enhanced Measurement;
 * keep "Page changes based on browser history events" enabled on the web stream.
 */

declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: (...args: unknown[]) => void;
  }
}

const MEASUREMENT_ID = (import.meta.env.VITE_GA_MEASUREMENT_ID ?? '').trim();
const VALID_ID = /^G-[A-Z0-9]{4,20}$/;
let initialised = false;

export function analyticsEnabled(): boolean {
  return VALID_ID.test(MEASUREMENT_ID) && typeof window !== 'undefined';
}

function getSanitizedLocation(): string {
  try {
    const url = new URL(window.location.href);
    const finding = url.searchParams.get('finding');
    const cleanParams = new URLSearchParams();
    if (finding) {
      cleanParams.set('finding', finding);
    }
    const qs = cleanParams.toString();
    return `${url.origin}${url.pathname}${qs ? `?${qs}` : ''}`;
  } catch {
    return `${window.location.origin}${window.location.pathname}`;
  }
}

export function initAnalytics(): void {
  if (initialised || !analyticsEnabled()) return;
  initialised = true;
  window.dataLayer = window.dataLayer || [];
  window.gtag = function gtag() {
    // gtag.js expects the Arguments object itself, not an array.
    // eslint-disable-next-line prefer-rest-params
    window.dataLayer!.push(arguments);
  };
  window.gtag('js', new Date());
  // GA4 sends the first page view itself, and its Enhanced Measurement ("page changes
  // based on browser history events", on by default) counts every in-app navigation.
  // Sending our own page_view as well would count each page twice, so we don't.
  const sanitizedLocation = getSanitizedLocation();
  window.gtag('config', MEASUREMENT_ID, {
    cookie_flags: 'SameSite=Lax;Secure',
    allow_google_signals: false,
    allow_ad_personalization_signals: false,
    page_location: sanitizedLocation,
  });
  const script = document.createElement('script');
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(MEASUREMENT_ID)}`;
  document.head.appendChild(script);
}

export function trackEvent(name: string, params: Record<string, string | number | boolean> = {}): void {
  if (!analyticsEnabled() || !window.gtag) return;
  window.gtag('event', name, params);
}
