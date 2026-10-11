# UI quality gate

Tick these for every UI change and paste the evidence (screenshots, audit output) in the PR. The same list drove `docs/specs/007`.

## Layout and devices
- [ ] Phone (320, 360, 390), tablet (768), laptop (1024, 1440), large (1920): no horizontal page scroll, nothing touches the screen edge, no text clipped.
- [ ] 200% text zoom (a 640px-wide window) still works.
- [ ] iOS: `100dvh`, safe-area insets respected, form text 16px (no zoom on focus), `-webkit-text-size-adjust` set.
- [ ] Very long words, URLs, commit hashes, emails and company names wrap (`overflow-wrap:anywhere`); labels and tags never break one letter per line.
- [ ] Tables become stacked cards on phones; wide code or diffs scroll inside their own box.

## Scrollbars
- [ ] Public pages hide the page scrollbar and show the thin reading-progress line; the factory hides it too. Inner scroll areas use the thin themed scrollbar. The default grey bar never shows.

## Interaction
- [ ] Everything works with keyboard only; focus is always visible; tab order follows reading order; dialogs and the menu trap focus and close on Esc.
- [ ] Touch targets are at least 44x44px (32px for dense inline staff controls).
- [ ] Hover effects exist only for `(hover:hover)`; nothing important is hover-only.
- [ ] `prefers-reduced-motion`: no animation, nothing hidden waiting for one.
- [ ] No JavaScript: public pages still show all content and navigation.

## Visual and content
- [ ] Colours come from `base.css` tokens; text contrast is AA (axe reports none).
- [ ] No emoji as icons; icons from `icons.js` with `aria-hidden`.
- [ ] Empty, loading and error states exist and read well.
- [ ] Public text obeys the content rule (outcomes, never mechanics).
- [ ] Console is clean, including CSP violations; fonts load from our own server.

## How we audit
Playwright at each width (overflow scan, tap targets), axe-core with `wcag2a/aa/21aa/best-practice`, a no-JS context, a reduced-motion context, keyboard walk-through, and torture inputs (300-character words, long emails).
