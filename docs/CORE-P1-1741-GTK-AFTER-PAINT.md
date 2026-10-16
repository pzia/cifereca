# #1741 — opt-in GTK after-paint observation (Library / Recent)

## What is measured

Only during **Settings → Diagnostics → Start detailed capture**, and only
for a mapped `MasterDetailPage` in Library or Recent, the Core schedules one
short-lived listener to the current `Gdk.FrameClock::after-paint` signal.

The reported phase `library.frame_wait` or `recent.frame_wait` measures
wall-clock **from the end of the synchronous render to the next GTK
frame-clock after-paint signal**. It is *not* the time to a scanned-out or
visible pixel, GPU presentation latency, input-to-photon latency, or a guarantee
that the widget's pixels actually changed in that particular frame.

The established `row_projection`, `facets`, `model_update`,
`selection` and passive `render_library` / `render_recent` measurements
remain separate. Inclusive intervals **must not be added blindly**.

## Boundedness and cancellation

- OFF by default; no GDK listener, timer, import of GLib or extra redraw on
  ordinary renders. No `queue_draw` / `request_phase`.
- Only `library` and `recent`, one listener per category. The next render
  replaces a previous unresolved observation.
- A listener is automatically removed on its first signal, after **1 second**
  with no signal, or on window close. If capture is stopped before delivery,
  the frame is discarded.
- An absent frame or unmapped widget is **missing data**, not an observed
  zero. Frames later than 1 second are discarded even if the timeout itself
  was postponed by main-thread contention.
- No book title, source, identity, token, route or window state is serialized.
  Existing bounded `UiPerfMonitor` controls n/p50/p95/max and simulated vs
  observed provenance. No network, persistence or compulsory tracing.

## Validation and interpretation

Unit tests with an injected clock and fake GDK signal cover a single paint,
replacement, expiration, disable, teardown, invalid page and absence of a
frame. CI GTK 1366/1600 must still be run on the exact PR HEAD.

This is a **new partial measurement**, not a decision that the synchronous
Library regression documented in #1741 has disappeared. A decision requires
enough real opt-in samples, a distribution and a comparable baseline; E2E
fixtures cannot be labelled as user observations.
