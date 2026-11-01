# Kas Control frontend

React / TypeScript, Base UI Dialog, Lucide and Sonner. All assets are local.

```powershell
pnpm.cmd install --frozen-lockfile
pnpm.cmd build
pnpm.cmd test
```

`dist/index.html` is the desktop entry point. The Python host serves this directory
and exposes the narrow asynchronous `window.pywebview.api` interface documented in
`src/api.ts`. The interface becomes available at `pywebviewready`; its 12-second
startup timeout does not limit backend operations.

For an isolated demonstration, run `pnpm.cmd dev` and open the printed local URL
with `?demo=1`. The fixture uses memory only and never invokes the Python bridge.
Without that explicit query, a missing bridge is an error, never a demo fallback.

Tests use the installed Microsoft Edge in headless mode, with an isolated browser
profile. Screenshots in `test-results` contain fixture data only. The tests verify
that creating a chat and assigning its command do not call `connect_chat`, and
check the minimum desktop size, close aliases and missing-bridge behavior.

Creation and command management preserve the active voice session. Only the
separate Connect button invokes `connect_chat`. No frontend state is written to
browser storage; external ChatGPT navigation goes through the Python bridge.

Every mutation refreshes the station overview and the relevant chat list while
controls remain disabled. An unsuccessful or uncertain mutation also attempts a
read-only refresh, without repeating the write. If that read fails, the status
header explicitly marks its information as stale and retains the original error.
