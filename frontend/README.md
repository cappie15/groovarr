# Groovarr frontend

React + TypeScript + Vite single-page app for the Groovarr admin UI. Dense,
information-first, Sonarr/Radarr-style layout — a left sidebar nav over a
content area, plain CSS, no component-kit dependency (see
`docs/00-research-and-architecture-review.md` §83).

**Phase 1 (Foundation) status**: this is a scaffold. Every routed section is
a placeholder page (heading + one-line description of what it will show).
No page is wired to real data yet — `src/api/client.ts` defines the shape
of the API client only.

## Layout

```
src/
├── api/
│   └── client.ts      # typed fetch wrapper, same-origin `/api`, no real endpoints yet
├── components/
│   ├── Icon.tsx            # small hand-rolled nav icons (no icon library)
│   ├── PlaceholderPage.tsx # shared "not yet implemented" page shell
│   └── Sidebar.tsx         # left nav, grouped per docs §83
├── pages/               # one file per routed section (see nav.ts)
├── styles/
│   ├── theme.css       # color tokens, light + prefers-color-scheme dark
│   └── app.css         # layout, sidebar, placeholder page styles
├── nav.ts               # single source of truth for sidebar groups/routes/descriptions
├── App.tsx              # route table
└── main.tsx              # entrypoint
```

## Routes

| Path | Page |
|---|---|
| `/` | Dashboard |
| `/playlists` | Playlists |
| `/tracks` | Tracks |
| `/wanted` | Wanted / Missing |
| `/incomplete` | Incomplete / Upgrade Wanted |
| `/search/automatic` | Automatic Search |
| `/search/manual` | Manual Search |
| `/activity` | Activity / Queue |
| `/history` | History |
| `/settings` | Settings |
| `/system` | System / Status |

## Development

```sh
npm install
npm run dev      # Vite dev server on :5173, proxies /api to :8000
npm run build    # type-check (tsc -b) + production build to dist/
npm run preview  # serve the production build locally
```

In production this app is built to static assets and served by the backend
container alongside the FastAPI app, which also serves `/api` — same origin,
no CORS configuration needed (architecture doc §5). The Vite dev-server
proxy target (`http://localhost:8000`) is a local development convenience
only.

## Conventions

- No Tailwind/MUI/Chakra/etc. — plain CSS, theme tokens in `theme.css`,
  layout in `app.css`. Keep the UI dense (small type, tight spacing) rather
  than adding visual chrome.
- Theme respects `prefers-color-scheme` automatically; there's no manual
  light/dark toggle in this phase.
- `nav.ts` is the single source of truth for sidebar groups, routes, and
  each page's placeholder description — update it first when adding or
  renaming a section, then keep the corresponding page file's copy in sync.
- Real API calls, data fetching hooks, and non-placeholder page content are
  out of scope for this phase — see `docs/00-research-and-architecture-
  review.md` §11 for what lands in each later phase.
