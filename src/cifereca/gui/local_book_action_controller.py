"""Local book actions for Library/Search detail, with explicit Core dependencies.

Maintains identity-scoped source/placement verification and deferred rechecks;
widgets and TaskRunner remain owned by the MainWindow facade (#1129).
"""
from __future__ import annotations

from ..plugin_api import Cancelled, UserFacingError


# Core classifies receipt, adoption, index and finalization failures.
# Dialogs translate the closed code; raw observations remain in the journal.
_NUMILOG_INDEX_REFUSAL_LABELS = {
    "invalid_target": "Cible de reprise invalide.",
    "receipt_missing": "Reçu de livraison introuvable.",
    "receipt_scope_invalid": "Propriétaire ou droit du reçu incohérent.",
    "receipt_invalid": "Métadonnées du reçu incohérentes.",
    "receipt_root_invalid": "Racine de livraison non conforme.",
    "path_not_canonical": "Emplacement initial ACSM non conforme.",
    "manual_receipt_missing": "Reçu d’import ACSM d’origine introuvable.",
    "manual_receipt_mismatch": "Reçu d’import ACSM incompatible avec la livraison.",
    "artifact_mismatch": "Empreinte ou taille de l’artefact incohérente.",
    "placement_missing": "Placement local d’origine introuvable ou ambigu.",
    "placement_conflict": "Placement local incompatible avec l’identité ou la source.",
    "catalogue_conflict": "Notice du catalogue contradictoire ou ambiguë.",
    "artifact_shared_elsewhere": "Même empreinte déjà utilisée par un placement du même profil ou de propriétaire inconnu.",
    "competing_receipts": "Plusieurs reçus concurrents pour cet achat.",
    "adoption_unavailable": "Validation du placement d’origine indisponible.",
    "selected_identity_changed": "L’identité sélectionnée n’est plus celle du reçu.",
    "target_changed": "La demande de reprise ne correspond plus au reçu.",
    "account_changed": "Le rattachement du compte Numilog a changé.",
    "receipt_conflict": "Reçus concurrents ou hors périmètre pour cet achat.",
    "directory_migration_pending": "Migration du répertoire en cours ou non finalisée.",
    "root_changed": "Racine de bibliothèque différente de celle du reçu.",
    "lock_unavailable": "Le verrou d’indexation est inaccessible ; consultez le journal.",
    "acquisition_busy": "Une autre acquisition ou indexation détient le verrou.",
    "receipt_verification_failed": "La vérification du reçu a échoué ; consultez le journal.",
    "placement_validation_failed": "La validation du placement a échoué ; consultez le journal.",
    "index_rejected": "Le catalogue a refusé le fichier livré ; consultez le journal.",
    "indexing_failed": "L’indexation locale a échoué ; consultez le journal.",
    "finalization_failed": "La finalisation du reçu a échoué ; consultez le journal.",
    "file_changed": "Fichier absent, déplacé ou empreinte différente du reçu.",
}


def _add_owned_download(window, detail, item, TaskPriority):
    """Expose a Core-admitted action, or an explicit *read-only* refusal.

    A remote-owned search result is not itself a delivery authorization.
    Without a unique Core target we cannot offer an active download, but
    silently omitting the action leaves the user unable to distinguish that
    state from a missing GUI feature (#3848).
    """
    resolve = getattr(window.service, 'owned_library_download_target', None)
    target = None
    if callable(resolve):
        try:
            target = resolve(item)
        except Exception:
            # A failed target resolution must not crash Library rendering or
            # put an upstream URL, credential or path into a GTK message.
            diagnostic = getattr(window, 'diagnostics', None)
            if diagnostic is not None:
                try:
                    diagnostic.add('UI', 'owned download target', 'error',
                                   detail={'code': 'owned_download_target_unavailable'})
                except Exception:
                    pass
    if target is None:
        # This indicator is not a right or an offer. Only an explicitly
        # recognized remote purchase (or Core's positive scoped possession)
        # can receive it; public notices and local-only files do not.
        if type(item) is dict and not any(
            item.get(key) for key in ('local', 'local_path', 'placement_id')
        ):
            identity = getattr(getattr(window.service, 'current_identity', None),
                               'identity_id', None)
            remote_purchase = item.get('state') == 'remote_owned'
            possessions = item.get('possessions')
            scoped_possession = (
                type(possessions) in (tuple, list)
                and any(
                    type(row) is dict
                    and row.get('identity_id') == identity
                    and row.get('kind') == 'owned'
                    and row.get('state') == 'active'
                    for row in possessions
                )
            )
            if remote_purchase or scoped_possession:
                detail.add_group('Acquisition', [
                    ('Téléchargement manuel',
                     'Indisponible : aucune cible exacte vérifiée pour ce compte et cette identité.'),
                ])
        return
    provider = getattr(target, 'provider_id', 'kobo')
    if provider not in {'kobo', 'numilog'}:
        return
    label = 'Numilog' if provider == 'numilog' else 'Kobo'
    resume = provider == 'numilog' and getattr(target, 'operation', None) == 'resume_index'
    button = window.action_button('Reprendre l’indexation Numilog' if resume else f'Télécharger ce livre {label}',
                                  'view-refresh-symbolic' if resume else 'folder-download-symbolic')
    button._cifereca_action_id = 'resume-index-numilog' if resume else 'download-owned-' + provider
    button.set_tooltip_text('Indexer le fichier déjà livré, sans téléchargement ni réseau.' if resume else
                            'Télécharger uniquement ce livre possédé, pour son compte et son identité. Aucun lot global.')
    failure = ('Reprise d’indexation indisponible. Vérifiez le fichier livré et son identité.' if resume else
               'Téléchargement ciblé indisponible. Actualisez les droits et vérifiez les fichiers locaux.')
    generation = getattr(window, '_identity_context_generation', None)
    def current():
        return (not getattr(window, '_closed', False)
                and getattr(window.service.current_identity, 'identity_id', None) == target.identity_id
                and getattr(window, '_identity_context_generation', None) == generation)
    def clicked(_button):
        if not current():
            return
        root = getattr(_button, 'get_root', None)
        if callable(root) and root() is None:
            return
        mapped = getattr(_button, 'get_mapped', None)
        if callable(mapped) and not mapped():
            return
        def work():
            if not current():
                raise RuntimeError('Le contexte du téléchargement a changé.')
            try:
                return window.service.obtain_owned_library_item(target)
            except Cancelled:
                raise
            except Exception as exc:
                if resume:
                    from ..core.numilog_materialization_checkpoint import (
                        NumilogIndexResumeRejected,
                    )
                    if isinstance(exc, NumilogIndexResumeRejected):
                        raise  # Only the closed reason code, never the error text.
                raise RuntimeError(failure) from None
        def done(result):
            if current() and getattr(result, 'path', None) is not None:
                window._activity_result('Indexation Numilog terminée.' if resume else 'Livre téléchargé dans la Bibliothèque.')
                window.refresh_library(False)
                window.refresh_recent()
                from .search_snapshot_refresh import invalidate_search_snapshot
                invalidate_search_snapshot(window)
        def failed(_exc):
            if current():
                message = failure
                if resume:
                    from ..core.numilog_materialization_checkpoint import (
                        NumilogIndexResumeRejected,
                    )
                    if isinstance(_exc, NumilogIndexResumeRejected):
                        reason = _exc.reason_code
                        message += " " + _NUMILOG_INDEX_REFUSAL_LABELS.get(
                            reason, "Vérification du placement à reprendre.",
                        )
                window.show_error(message, 'Reprise d’indexation Numilog' if resume else 'Téléchargement du livre possédé')
        window.runner.submit('Reprise d’indexation Numilog…' if resume else f'Téléchargement du livre {label} possédé…',
                             work, done, failed, control=_button, priority=TaskPriority.USER_ACTION,
                             kind='index' if resume else 'obtain', origin='user')
    button.connect('clicked', clicked)
    detail.add_actions([button])


def add_local_book_actions(window, detail, item, *, Gtk, Gio, ArtifactRouteRequest, TaskPriority, identity_has_attested_possession):
    if getattr(window, '_closed', False):
        return
    _add_owned_download(window, detail, item, TaskPriority)
    placement_id=str(item.get('placement_id') or '')
    identity_id=str(item.get('identity_id') or '')
    if not placement_id:
        provider=item.get('provider_id') or item.get('source_provider')
        credential=item.get('source_credential') or (item.get('metadata') or {}).get('source_credential')
        item_id=item.get('item_id') or item.get('record_id')
        current=getattr(window.service,'current_identity',None)
        if not provider:
            if item.get('title'): window._add_catalogue_action(detail, item)
            return
        if not credential or not item_id or not current:
            window._add_catalogue_action(detail, item); return
        placement=window.service.local_placement_for_source(provider_id=provider,credential_id=credential,
            item_id=item_id,identity_id=current.identity_id)
        if not placement:
            window._add_catalogue_action(detail, item); return
        placement_id=placement['placement_id'];identity_id=current.identity_id
    if not identity_id:
        window._add_catalogue_action(detail, item); return
    try:window.service.local_book_path(placement_id,identity_id=identity_id)
    except Exception:
        window._add_catalogue_action(detail, item); return
    opening=window.action_button('Ouvrir','document-open-symbolic')
    opening._cifereca_action_id = 'open-local'
    def open_book(_button):
        if getattr(window, '_closed', False):
            return
        try:
            # Recheck existence and identity at activation, then launch without a shell.
            uri=window.service.local_book_path(placement_id,identity_id=identity_id).as_uri()
            Gio.AppInfo.launch_default_for_uri(uri,None)
        except Exception:
            if not getattr(window, '_closed', False):
                # Launchers may return private file paths or command details.
                window.show_error('Ouverture locale indisponible', 'Ouverture du livre impossible')
    opening.connect('clicked',open_book)
    buttons=[opening]
    source_provider = str(item.get('provider_id') or item.get('source_provider') or 'local')
    source_credential = str(item.get('source_credential') or (item.get('metadata') or {}).get('source_credential') or '')
    source_item_id = str(item.get('item_id') or item.get('source_item_id') or (item.get('record_id') or ''))
    known_possessions = ()
    manager = getattr(window, 'plugin_manager', None)
    library_read = manager.broker('library.read') if manager else None
    if library_read is not None:
        try:
            logical = library_read.book(source_item_id) if source_item_id else None
            if logical is None and item.get('artifact_id'):
                logical = library_read.for_artifact(str(item.get('artifact_id')))
            known_possessions = tuple(logical.possessions) if logical is not None else ()
        except Exception:
            window.diagnostics.add(
                'UI', 'read known possessions', 'error',
                detail={'code': 'known_possessions_unavailable'},
            )
    for target in window.service.identities:
        if target.identity_id==identity_id:continue
        # A current Core possession is authoritative even when there is no
        # local file yet for the target identity.
        if source_item_id and identity_has_attested_possession(
            known_possessions,
            identity_id=target.identity_id,
            provider_id=source_provider,
            source_item_id=source_item_id,
            credential_id=source_credential,
        ):
            continue
        # Keep the placement fallback for older/local-only records that are
        # not represented in the logical possession projection.
        if source_item_id:
            try:
                already_owned = window.service.local_placement_for_source(
                    provider_id=source_provider, credential_id=source_credential,
                    item_id=source_item_id, identity_id=target.identity_id,
                ) is not None
            except Exception:
                already_owned = False
            if already_owned:
                continue
        button=window.action_button('Envoyer à '+target.label,'document-send-symbolic')
        button._cifereca_action_id = 'send-local:'+target.identity_id
        def send(_button,target_id=target.identity_id):
            if getattr(window, '_closed', False):
                return
            def work():
                window.service.local_book_path(placement_id,identity_id=identity_id)
                return window.service.materialize_to_identity(placement_id,target_id)
            window.runner.submit('Envoi du fichier local…',work,
                lambda _result:(window._activity_result('Livre envoyé'),window.refresh_recent(),window.refresh_library(False)),
                control=_button,priority=TaskPriority.USER_ACTION,kind='materialize')
        button.connect('clicked',send);buttons.append(button)
    routing = manager.broker('artifact.routing') if manager else None
    context = window._book_action_context(item) if routing is not None else None
    if routing is not None and context is not None and context.artifact is not None:
        try:
            route_request = ArtifactRouteRequest(context.artifact, identity_id=identity_id or None)
            route_targets = tuple(routing.targets(route_request))
        except Exception:
            route_targets = ()
            window.diagnostics.add(
                'UI', 'artifact routing targets', 'error',
                detail={'code': 'artifact_targets_unavailable'},
            )
        if route_targets:
            menu = Gtk.MenuButton(label='Envoyer vers…')
            menu._cifereca_action_id = 'artifact-routing'
            menu.set_tooltip_text('Envoyer cet artefact vers une cible de stockage compatible')
            popover = Gtk.Popover()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.set_margin_top(8); box.set_margin_bottom(8); box.set_margin_start(8); box.set_margin_end(8)
            for target in route_targets:
                target_button = Gtk.Button(label=str(target.target.label or target.target.target_id))
                target_button.add_css_class('flat')
                def route(_button, target_id=target.target.target_id):
                    if getattr(window, '_closed', False):
                        return
                    request = ArtifactRouteRequest(context.artifact, target_id=target_id, identity_id=identity_id or None)
                    def work():
                        plan = routing.plan(request)
                        return routing.transfer(plan)
                    def failed(exc):
                        if getattr(window, '_closed', False):
                            return
                        # Only explicit user-facing errors have a safe message.
                        message = (exc.user_message if isinstance(exc, UserFacingError)
                                   else 'Envoi vers le stockage indisponible')
                        window.show_error(message, 'Envoi impossible')
                    window.runner.submit('Envoi vers le stockage…', work,
                        lambda _result:(window._activity_result('Livre envoyé'), window.refresh_library(False), window.refresh_recent()),
                        failed, control=_button,
                        priority=TaskPriority.USER_ACTION, kind='artifact-route')
                target_button.connect('clicked', route); box.append(target_button)
            popover.set_child(box); menu.set_popover(popover); buttons.append(menu)
    detail.add_actions(buttons)
