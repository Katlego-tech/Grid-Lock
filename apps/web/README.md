# apps/web — responder console

The ranked queue responders work from (US5). React 19 + Vite, on Node 24 (`.nvmrc`).

```bash
npm ci            # from package-lock.json
npm run dev       # http://localhost:5173
npm run lint      # eslint, prettier --check, tsc
npm test          # vitest
npm run build     # production bundle in dist/
```

Built against `docs/design/assets/responder-queue.png`. Colours, type and spacing come from
`docs/design/assets/tokens.css`, imported directly, never copied. So far this is the frame,
the top bar of the visual reference. The queue itself renders from `GET /api/queue` once
ingest-api serves it.
