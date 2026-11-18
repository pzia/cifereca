# UI page lifetime (v1, #4010)

A `ui.page` factory may opt in by attaching `PageLifecycleV1` from
`cifereca.plugin_api.page_lifecycle` to its widget as
`cifereca_page_lifecycle_v1`. Existing contributions without it retain their
historical refresh paths. No GTK object, dropdown signal or Core storage
access is exposed by the notification contract.

- `on_context_changed(event)`: invalidate local actions, private rows and
  stored future callbacks immediately, even while hidden. Do not perform
  network work, refresh a source or start an acquisition. All affected
  generations are retired before the first hook. `account_scoped=True`
  opts in to the exact secondary account coordinates; otherwise they are
  absent, and secondary changes do not invalidate the view.
- `on_show(event)`: render the currently visible identity from local/cached
  data; use the explicit identity and account scope, never infer authorization.
  Same-route duplicate signals are coalesced. Hidden changes accrue a single
  dirty projection; native private models are retired before the visible
  hook is dispatched on identity change. Cache/IO policy remains separate.
- Before publishing a delayed result or admitting a new action from a view,
  require `event.is_current()`. It fences the mounted route/visibility/
  generation and window alive state. Navigation A/B/A, replacement, hidden
  route, teardown and failure retire earlier handles. This is NOT a Core
  lease and must not cancel an already admitted business operation.
- `on_dispose()` is optional, called on detach/rebuild for registered hooks.
  Release subscriptions; do not perform provider I/O. Close invalidates
  handles immediately. Explicit close disposal is to be connected once the
  Notices owner releases the shared window_close.py controller.

Hooks are trusted in-process code, and not an enforcement sandbox; exceptions
do not propagate into the shell and diagnostics carry closed reason codes.
The native Sync page is the initial consumer; other old pages may keep their
legacy subscriptions until migrated. No new network behavior is authorized.
