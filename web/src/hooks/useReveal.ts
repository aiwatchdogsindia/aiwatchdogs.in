import { useEffect } from 'react';

const SELECTOR = '.app-main section, .app-main [data-reveal]';

/**
 * Scroll reveal for page sections. Sections that start below the fold get the
 * `rv` class and rise into place the first time they enter the viewport.
 * Anything already on screen is left alone, so nothing flashes on load, and
 * without JavaScript no content is ever hidden.
 */
export function useReveal(routeKey: string) {
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;

    const seen = new WeakSet<Element>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add('rv-in');
          io.unobserve(entry.target);
        }
      },
      { rootMargin: '0px 0px -8% 0px', threshold: 0.04 },
    );

    const scan = () => {
      document.querySelectorAll(SELECTOR).forEach((el) => {
        if (seen.has(el)) return;
        seen.add(el);
        // Nested sections animate with their parent
        if (el.parentElement?.closest('.rv')) return;
        const top = el.getBoundingClientRect().top;
        if (top < window.innerHeight * 0.92) return;
        el.classList.add('rv');
        io.observe(el);
      });
    };

    scan();
    // Lazy routes and data-driven sections mount after the first paint
    let queued = 0;
    const mo = new MutationObserver(() => {
      if (queued) return;
      queued = requestAnimationFrame(() => {
        queued = 0;
        scan();
      });
    });
    const main = document.querySelector('.app-main');
    if (main) mo.observe(main, { childList: true, subtree: true });

    return () => {
      io.disconnect();
      mo.disconnect();
      cancelAnimationFrame(queued);
      document.querySelectorAll('.rv').forEach((el) => el.classList.remove('rv', 'rv-in'));
    };
  }, [routeKey]);
}
