# Third-Party License Audit

Status: recorded for the oc8 Community release under LGPL-3.0-or-later. Covers `backend/` and `frontend/` only — `capas/*` is out of scope (see NOTICE and [LICENSE](LICENSE)).

## Purpose

oc8 Community is licensed under the GNU Lesser General Public License v3.0 or later (LGPLv3+). This document records the third-party dependency audit performed before that decision, checking whether any dependency's license (GPL, AGPL, SSPL, or similarly viral copyleft terms) would prevent distributing oc8 Community under LGPLv3.

## Result

**No conflicts found.** No production dependency of `backend/` or `frontend/` carries a GPL, AGPL, SSPL, or other strong-copyleft license. All production dependencies are permissive (MIT, BSD, Apache-2.0, ISC, 0BSD, Unlicense, Python-2.0/PSF) or weak/file-level copyleft (LGPL-3.0, MPL-2.0), both of which are compatible with linking into, and distributing alongside, an LGPLv3 work.

## Notable findings

- **`psycopg` / `psycopg-binary` (backend, PostgreSQL driver) — `LGPL-3.0-only`.** Same license family as oc8 Community itself; using an LGPLv3 library from an LGPLv3 project is unambiguously compatible.
- **`certifi`, `lightningcss`, `lightningcss-darwin-arm64` — `MPL-2.0`.** Mozilla Public License 2.0 is file-level weak copyleft: it only requires that modifications *to MPL-licensed files themselves* stay under MPL, and explicitly permits combination with other-licensed code in a "Larger Work" (MPL-2.0 §3.3). No dependency here is forked or modified, so this is a non-issue in practice.
- **`redis` (backend, Python client library) is MIT-licensed.** This is unrelated to the Redis *server's* license (RSALv2/SSPL since Redis 7.4) — oc8 only depends on the client library at import time and talks to a Redis server over the network as a separate, independently-licensed service; the server binary is never bundled or distributed by oc8.
- **`docker` (backend, Docker Engine API client) is Apache-2.0.** Same distinction as Redis: oc8 talks to the Docker Engine over its API — the Docker Engine itself is not bundled or distributed.
- **No AGPL/SSPL/BUSL/Commons-Clause dependency was found anywhere in either dependency tree** — these are the licenses that would have been the actual red flags. Plain GPL is already rare among widely-used Python/JS libraries, since it does not permit linking into non-GPL works and most library authors choose LGPL/MPL/Apache/MIT instead.

## Scope note: `capas/*`

This audit does not cover `capas/*`: each capa carries its own license per its manifest (see `capas/README.md`) and is a separate work, not a combined/derivative work of the LGPLv3 core.

## Methodology

- Backend: `cd backend && uv sync && uv run --with pip-licenses pip-licenses --format=json` against the resolved production dependency graph (dev-only tools such as pytest, mypy, ruff, testcontainers are excluded — they are never distributed with oc8).
- Frontend: `cd frontend && npx license-checker --production --json` against the resolved `dependencies` graph (`devDependencies` excluded for the same reason).
- Every reported license string was checked against the [SPDX license list](https://spdx.org/licenses/) and the FSF's [license compatibility guidance](https://www.gnu.org/licenses/license-list.html).
- Last run: 2026-08-15. Corrected 2026-08-16: this audit's own commit had accidentally dropped `python-docx`, `openpyxl`, and `python-pptx` from `backend/pyproject.toml`'s dependency list (Office document text extraction, used by the Microsoft 365 plugin's connector and MCP tool bridge, both of which import into and run inside the backend process — not out of scope the way `plugins/*`-only dependencies are). Restored, along with their transitive dependencies (`et_xmlfile`, `lxml`, `pillow`, `xlsxwriter`) — all seven are MIT/BSD-family permissive, no conflict with LGPLv3.

## Backend production dependencies (87 packages)

**3-Clause BSD License**

- protobuf 7.35.1

**Apache Software License**

- googleapis-common-protos 1.75.0
- requests 2.34.2

**Apache Software License; BSD License**

- python-dateutil 2.9.0.post0

**Apache Software License; MIT License**

- uvloop 0.22.1

**Apache-2.0**

- asyncpg 0.31.0
- docker 7.2.0
- grpcio 1.83.0
- opentelemetry-api 1.44.0
- opentelemetry-exporter-otlp 1.44.0
- opentelemetry-exporter-otlp-proto-common 1.44.0
- opentelemetry-exporter-otlp-proto-grpc 1.44.0
- opentelemetry-exporter-otlp-proto-http 1.44.0
- opentelemetry-instrumentation 0.65b0
- opentelemetry-instrumentation-asgi 0.65b0
- opentelemetry-instrumentation-fastapi 0.65b0
- opentelemetry-instrumentation-httpx 0.65b0
- opentelemetry-instrumentation-logging 0.65b0
- opentelemetry-instrumentation-sqlalchemy 0.65b0
- opentelemetry-proto 1.44.0
- opentelemetry-sdk 1.44.0
- opentelemetry-semantic-conventions 0.65b0
- opentelemetry-util-http 0.65b0
- python-multipart 0.0.32

**Apache-2.0 OR BSD-2-Clause**

- packaging 26.2

**Apache-2.0 OR BSD-3-Clause**

- cryptography 50.0.0

**BSD License**

- XlsxWriter 3.2.9
- asgiref 3.12.1
- httpx 0.28.1

**BSD-2-Clause**

- wrapt 2.3.0

**BSD-3-Clause**

- MarkupSafe 3.0.3
- click 8.4.2
- httpcore 1.0.9
- httpcore2 2.9.1
- httpx2 2.9.1
- idna 3.18
- lxml 6.1.1
- pycparser 3.0
- pypdf 6.14.2
- python-dotenv 1.2.2
- sse-starlette 3.4.6
- starlette 1.3.1
- uvicorn 0.52.1
- websockets 17.0.1

**LGPL-3.0-only**

- psycopg 3.3.4
- psycopg-binary 3.3.4

**MIT**

- PyJWT 2.13.0
- SQLAlchemy 2.0.51
- alembic 1.18.5
- annotated-doc 0.0.5
- annotated-types 0.8.0
- anyio 4.14.2
- argon2-cffi 25.1.0
- argon2-cffi-bindings 25.1.0
- attrs 26.1.0
- charset-normalizer 3.4.9
- croniter 6.2.4
- fastapi 0.141.1
- httptools 0.8.0
- jsonschema 4.26.0
- jsonschema-specifications 2025.9.1
- pgvector 0.5.0
- pydantic 2.13.4
- pydantic-settings 2.14.2
- pydantic_core 2.46.4
- redis 8.1.0
- referencing 0.37.0
- rpds-py 2026.6.3
- truststore 0.10.4
- typing-inspection 0.4.2
- urllib3 2.7.0

**MIT AND PSF-2.0**

- greenlet 3.5.4

**MIT License**

- Mako 1.3.12
- PyYAML 6.0.3
- et_xmlfile 2.0.0
- h11 0.16.0
- mcp 2.0.0
- mcp-types 2.0.0
- openpyxl 3.1.5
- python-docx 1.2.0
- python-pptx 1.0.2
- six 1.17.0
- watchfiles 1.2.0

**MIT-0**

- cffi 2.1.0

**MIT-CMU**

- pillow 12.3.0

**Mozilla Public License 2.0 (MPL 2.0)**

- certifi 2026.7.22

**PSF-2.0**

- typing_extensions 4.16.0

## Frontend production dependencies (267 packages)

**0BSD** (1): tslib@2.8.1

**Apache-2.0** (4): baseline-browser-mapping@2.10.21, class-variance-authority@0.7.1, detect-libc@2.1.2, typescript@5.9.3

**BSD-3-Clause** (5): d3-ease@3.0.1, diff@8.0.4, react-transition-group@4.4.5, source-map-js@1.2.1, source-map@0.7.6

**CC-BY-4.0** (1): caniuse-lite@1.0.30001790

**ISC** (19): ansis@4.2.0, d3-array@3.2.4, d3-color@3.1.0, d3-format@3.1.2, d3-interpolate@3.0.1, d3-path@3.1.0, d3-scale@4.0.2, d3-shape@3.2.0, d3-time-format@4.1.0, d3-time@3.1.0, d3-timer@3.0.1, electron-to-chromium@1.5.344, graceful-fs@4.2.11, internmap@2.0.3, lru-cache@5.1.1, lucide-react@0.575.0, picocolors@1.1.1, semver@6.3.1, yallist@3.1.1

**MIT** (232): @babel/code-frame@7.27.1, @babel/code-frame@7.29.0, @babel/compat-data@7.29.0, @babel/core@7.29.0, @babel/generator@7.29.1, @babel/helper-compilation-targets@7.28.6, @babel/helper-globals@7.28.0, @babel/helper-module-imports@7.28.6, @babel/helper-module-transforms@7.28.6, @babel/helper-string-parser@7.27.1, @babel/helper-validator-identifier@7.28.5, @babel/helper-validator-option@7.27.1, @babel/helpers@7.29.2, @babel/parser@7.29.2, @babel/runtime@7.29.2, @babel/template@7.28.6, @babel/traverse@7.29.0, @babel/types@7.29.0, @date-fns/tz@1.4.1, @esbuild/darwin-arm64@0.25.12, @esbuild/darwin-arm64@0.27.7, @floating-ui/core@1.7.5, @floating-ui/dom@1.7.6, @floating-ui/react-dom@2.1.8, @floating-ui/utils@0.2.11, @hookform/resolvers@5.2.2, @jridgewell/gen-mapping@0.3.13, @jridgewell/remapping@2.3.5, @jridgewell/resolve-uri@3.1.2, @jridgewell/sourcemap-codec@1.5.5, @jridgewell/trace-mapping@0.3.31, @oozcitak/dom@2.0.2, @oozcitak/infra@2.0.2, @oozcitak/url@3.0.0, @oozcitak/util@10.0.0, @oxc-project/types@0.133.0, @radix-ui/number@1.1.1, @radix-ui/primitive@1.1.3, @radix-ui/react-accordion@1.2.12, @radix-ui/react-alert-dialog@1.1.15, @radix-ui/react-arrow@1.1.7, @radix-ui/react-aspect-ratio@1.1.8, @radix-ui/react-avatar@1.1.11, @radix-ui/react-checkbox@1.3.3, @radix-ui/react-collapsible@1.1.12, @radix-ui/react-collection@1.1.7, @radix-ui/react-compose-refs@1.1.2, @radix-ui/react-context-menu@2.2.16, @radix-ui/react-context@1.1.2, @radix-ui/react-context@1.1.3, @radix-ui/react-dialog@1.1.15, @radix-ui/react-direction@1.1.1, @radix-ui/react-dismissable-layer@1.1.11, @radix-ui/react-dropdown-menu@2.1.16, @radix-ui/react-focus-guards@1.1.3, @radix-ui/react-focus-scope@1.1.7, @radix-ui/react-hover-card@1.1.15, @radix-ui/react-id@1.1.1, @radix-ui/react-label@2.1.8, @radix-ui/react-menu@2.1.16, @radix-ui/react-menubar@1.1.16, @radix-ui/react-navigation-menu@1.2.14, @radix-ui/react-popover@1.1.15, @radix-ui/react-popper@1.2.8, @radix-ui/react-portal@1.1.9, @radix-ui/react-presence@1.1.5, @radix-ui/react-primitive@2.1.3, @radix-ui/react-primitive@2.1.4, @radix-ui/react-progress@1.1.8, @radix-ui/react-radio-group@1.3.8, @radix-ui/react-roving-focus@1.1.11, @radix-ui/react-scroll-area@1.2.10, @radix-ui/react-select@2.2.6, @radix-ui/react-separator@1.1.8, @radix-ui/react-slider@1.3.6, @radix-ui/react-slot@1.2.3, @radix-ui/react-slot@1.2.4, @radix-ui/react-switch@1.2.6, @radix-ui/react-tabs@1.1.13, @radix-ui/react-toggle-group@1.1.11, @radix-ui/react-toggle@1.1.10, @radix-ui/react-tooltip@1.2.8, @radix-ui/react-use-callback-ref@1.1.1, @radix-ui/react-use-controllable-state@1.2.2, @radix-ui/react-use-effect-event@0.0.2, @radix-ui/react-use-escape-keydown@1.1.1, @radix-ui/react-use-is-hydrated@0.1.0, @radix-ui/react-use-layout-effect@1.1.1, @radix-ui/react-use-previous@1.1.1, @radix-ui/react-use-rect@1.1.1, @radix-ui/react-use-size@1.1.1, @radix-ui/react-visually-hidden@1.2.3, @radix-ui/rect@1.1.1, @rolldown/binding-darwin-arm64@1.0.3, @rolldown/pluginutils@1.0.1, @standard-schema/utils@0.3.0, @tabby_ai/hijri-converter@1.0.5, @tailwindcss/node@4.2.4, @tailwindcss/oxide-darwin-arm64@4.2.4, @tailwindcss/oxide@4.2.4, @tailwindcss/vite@4.2.4, @tanstack/history@1.162.0, @tanstack/query-core@5.101.1, @tanstack/react-query@5.101.1, @tanstack/react-router@1.170.16, @tanstack/react-start-client@1.168.14, @tanstack/react-start-rsc@0.1.25, @tanstack/react-start-server@1.167.20, @tanstack/react-start@1.168.26, @tanstack/react-store@0.9.3, @tanstack/router-core@1.171.13, @tanstack/router-generator@1.167.17, @tanstack/router-plugin@1.168.18, @tanstack/router-utils@1.162.2, @tanstack/start-client-core@1.170.12, @tanstack/start-fn-stubs@1.162.0, @tanstack/start-plugin-core@1.171.18, @tanstack/start-server-core@1.169.15, @tanstack/start-storage-context@1.167.15, @tanstack/store@0.9.3, @tanstack/virtual-file-routes@1.162.0, @types/d3-array@3.2.2, @types/d3-color@3.1.3, @types/d3-ease@3.0.2, @types/d3-interpolate@3.0.4, @types/d3-path@3.1.1, @types/d3-scale@4.0.9, @types/d3-shape@3.1.8, @types/d3-time@3.0.4, @types/d3-timer@3.0.2, @types/node@22.19.17, @types/react-dom@19.2.3, @types/react@19.2.14, aria-hidden@1.2.6, babel-dead-code-elimination@1.0.12, browserslist@4.28.2, chokidar@5.0.0, clsx@2.1.1, cmdk@1.1.1, convert-source-map@2.0.0, cookie-es@3.1.1, crossws@0.4.5, csstype@3.2.3, date-fns-jalali@4.1.0-0, date-fns@4.1.0, debug@4.4.3, decimal.js-light@2.5.1, detect-node-es@1.1.0, dom-helpers@5.2.1, embla-carousel-react@8.6.0, embla-carousel-reactive-utils@8.6.0, embla-carousel@8.6.0, enhanced-resolve@5.20.1, esbuild@0.25.12, esbuild@0.27.7, escalade@3.2.0, eventemitter3@4.0.7, exsolve@1.0.8, fast-equals@5.4.0, fdir@6.5.0, fetchdts@0.1.7, fsevents@2.3.3, gensync@1.0.0-beta.2, get-nonce@1.0.1, get-tsconfig@4.14.0, globrex@0.1.2, h3@2.0.1-rc.20, input-otp@1.4.2, jiti@2.6.1, jiti@2.7.0, js-tokens@4.0.0, js-yaml@4.1.1, jsesc@3.1.0, json5@2.2.3, lodash@4.18.1, loose-envify@1.4.0, magic-string@0.30.21, ms@2.1.3, nanoid@3.3.12, node-releases@2.0.38, object-assign@4.1.1, pathe@2.0.3, picomatch@4.0.4, postcss@8.5.15, prettier@3.8.3, prop-types@15.8.1, react-day-picker@9.14.0, react-dom@19.2.5, react-hook-form@7.73.1, react-is@16.13.1, react-is@18.3.1, react-remove-scroll-bar@2.3.8, react-remove-scroll@2.7.2, react-resizable-panels@4.10.0, react-smooth@4.0.4, react-style-singleton@2.2.3, react@19.2.5, readdirp@5.0.0, recharts-scale@0.4.5, recharts@2.15.4, resolve-pkg-maps@1.0.0, rolldown@1.0.3, rou3@0.8.1, scheduler@0.27.0, seroval-plugins@1.5.4, seroval@1.5.4, sonner@2.0.7, srvx@0.11.15, srvx@0.11.16, tailwind-merge@3.5.0, tailwindcss@4.2.4, tapable@2.3.3, tiny-invariant@1.3.3, tinyglobby@0.2.17, tsconfck@3.1.6, tsx@4.21.0, tw-animate-css@1.4.0, ufo@1.6.3, undici-types@6.21.0, unplugin@3.0.0, update-browserslist-db@1.2.3, use-callback-ref@1.3.3, use-sidecar@1.1.3, use-sync-external-store@1.6.0, vaul@1.1.2, vite-tsconfig-paths@6.1.1, vite@8.0.16, vitefu@1.1.3, webpack-virtual-modules@0.6.2, xmlbuilder2@4.0.3, zod@3.25.76, zod@4.4.3

**MIT AND ISC** (1): victory-vendor@36.9.2

**MPL-2.0** (2): lightningcss-darwin-arm64@1.32.0, lightningcss@1.32.0

**Python-2.0** (1): argparse@2.0.1

**Unlicense** (1): isbot@5.1.39

