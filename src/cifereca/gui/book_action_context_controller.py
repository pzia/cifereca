"""Build public book-detail UI action context for Core plugin contributions (#1130).

MainWindow owns the active route and storage projection. The builder accepts
public Plugin API types and the current Core row converter explicitly, without
importing GTK or a plugin implementation.
"""
from __future__ import annotations


def book_action_context(window, item, *, Path, ArtifactRef, ArtifactFormat, UiActionContext, book_ref_from_row):
    # Capture host provenance before artifact/storage lookups. Metadata from a
    # provider is not authority to select another profile for this UI action.
    identity = getattr(window.service, "current_identity", None)
    identity_id = getattr(identity, "identity_id", "")
    identity_id = identity_id if isinstance(identity_id, str) else ""
    metadata = dict(item.get("metadata") or {}) if isinstance(item, dict) else {}
    book = book_ref_from_row(item)
    artifact = None
    path_value = item.get("local_path") or metadata.get("path")
    artifact_id = str(item.get("artifact_id") or "")
    if path_value and artifact_id:
        path = Path(str(path_value))
        size = 0
        try: size = path.stat().st_size
        except OSError: pass
        sha = ""
        try:
            row = next((r for r in window.service.storage.artifacts() if str(r.get("artifact_id") or "") == artifact_id), None)
            if row: sha = str(row.get("sha256") or "")
        except Exception: pass
        # The selected local row is descriptive. Only the Core Placement
        # database can supply the source coordinates for a plugin action.
        # Require a UNIQUE exact path+artifact+identity; do not select the
        # first file of a global SHA or accept provider metadata as authority.
        selected_pid = None
        if identity_id and path.is_absolute():
            try:
                scoped = [r for r in window.service.storage.placements(state="present")
                          if r.get("artifact_id") == artifact_id
                          and r.get("identity_id") == identity_id
                          and r.get("path") == str(path)]
                if len(scoped) == 1 and type(scoped[0].get("placement_id")) is str:
                    selected_pid = scoped[0]["placement_id"]
            except Exception:
                pass
        fmt = str(item.get("media_format") or path.suffix.lstrip(".") or "")
        artifact = ArtifactRef(artifact_id=artifact_id, book_id=book.book_id or None,
            format=ArtifactFormat("application/epub+zip" if fmt.lower() == "epub" else "application/octet-stream", fmt),
            size_bytes=size, sha256=sha, filename=path.name,
            identity_id=identity_id if selected_pid is not None else None,
            placement_id=selected_pid)
    return UiActionContext(target="book.detail", route=window.view_stack.get_visible_child_name() or "", book=book, artifact=artifact, metadata=metadata, identity_id=identity_id)

