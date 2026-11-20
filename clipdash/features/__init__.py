"""Feature modules: self-contained additions that plug into the core through fixed extension points, so several
can be developed side by side without touching the same files.

A feature is one module in this package, e.g. clipdash/features/analytics.py. It may:
  * expose `router = fastapi.APIRouter()`  → mounted automatically by app.py (prefix your paths, e.g. /api/analytics/…)
  * register schema at import time via db.register_table(ddl) / db.register_column(table, name, decl)
  * register settings keys via config.register_settings({"key": default, …}) so PUT /api/settings accepts them
  * subscribe to pipeline events via events.on("clip.ready", fn)  (see clipdash/events.py for the list)
  * register background jobs via jobs.HANDLERS["my_job"] = fn(payload, progress) inside a `register()` function
  * ship UI as clipdash/web/features/<name>.js, loaded after app.js on every page. The core's helpers are globals
    ($, $$, esc, get, post, put, del, toast, modal, closeModal, badge, label, fmtTime, fmtDate, fmtNum, campaign(), S,
    render(), refreshState()). Extension points in app.js:
      VIEWS[name] = async (el, arg) => …          a page at #/<name>
      PANES[name] + SETTINGS_TABS.push([name, 'Label'])   a tab under Settings (#/settings/<name>)
      TODAY_EXTRAS.push((t, c) => html)           a card inserted on Today above "Needs attention"
      REVIEW_EXTRAS.push((ctx) => void)           runs when the Review modal opens; ctx = {cl, m, v, camp, save, gate, nextId, setPlayer}
      REVIEW_KEYS['j'] = (ctx) => void            extra keyboard shortcuts in the Review modal
      SOURCE_EXTRAS.push((src, card, c) => void)  runs for every source card
      NAV_EXTRAS.push({href, label, svg})         an extra sidebar entry (use sparingly)

Modules are imported by load() in alphabetical order before the database schema is applied."""
from __future__ import annotations

import importlib
import logging
import pkgutil

log = logging.getLogger("clipdash.features")
_loaded: dict[str, object] = {}


def load() -> dict[str, object]:
    """Import every feature module once; a broken feature is logged and skipped, never fatal."""
    if _loaded:
        return _loaded
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        try:
            _loaded[info.name] = importlib.import_module(f"{__name__}.{info.name}")
        except Exception:  # noqa: BLE001
            log.exception("feature %s failed to import", info.name)
    return _loaded


def register_all(app) -> None:
    """Mount routers and call each module's register() (jobs, event handlers) once the app exists."""
    for name, mod in load().items():
        router = getattr(mod, "router", None)
        if router is not None:
            app.include_router(router)
        reg = getattr(mod, "register", None)
        if callable(reg):
            try:
                reg()
            except Exception:  # noqa: BLE001
                log.exception("feature %s register() failed", name)
