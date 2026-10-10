# Visual references

Each PNG here is the image a UI lane builds against, named by path in its design doc. The `.html`
beside it is the source. Edit the HTML, re-render, and commit both together, so the image and its
source never disagree.

| Image | Source | Built against it |
| --- | --- | --- |
| `responder-queue.png` | `responder-queue.html` | `apps/web`, the responder console (`responder-console.md`). Frames: A desk populated · B 375 px populated · C desk empty · D desk, connection lost and a failed acknowledge · E 375 px, Needs review shown · F 375 px empty |
| `reporter-app.png` | `reporter-app.html` | `apps/mobile`, the reporter app (`reporter-app.md`). Frames, all 375 px: A compose empty · B location attached · C location permission denied · D location allowed but no fix · E no signal, saved and not sent · F received, with its reference id · G not accepted, text kept |

`tokens.css` is GridLock's one token set (colour, type, space). Both client surfaces derive their
theme from it.

## Re-rendering

Any Chromium (Puppeteer or Playwright) at a 1936 px viewport, full-page capture, with the three Google Fonts loaded
(Atkinson Hyperlegible, Barlow Condensed, IBM Plex Mono). For example, with Puppeteer:

```js
const page = await browser.newPage();
await page.setViewport({ width: 1936, height: 1000 });
await page.goto('file://' + path.resolve('docs/design/assets/responder-queue.html'), { waitUntil: 'networkidle0' });
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: 'docs/design/assets/responder-queue.png', fullPage: true });
```
