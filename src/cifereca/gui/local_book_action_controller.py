"""Local book actions for Library/Search detail, with explicit Core dependencies.

Maintains identity-scoped source/placement verification and deferred rechecks;
widgets and TaskRunner remain owned by the MainWindow facade (#1129).
"""
from __future__ import annotations

from ..plugin_api import Cancelled, UserFacingError


def _add_owned_download(window, detail, item, TaskPriority):
    """Expose a Core-admitted action, or an explicit *read-only* refusal.

    A remote-owned search result is not itself a delivery authorization.
    Without a unique Core target we cannot offer an active download, but
    silently omitting the action leaves the user unable to distinguish that
    state from a missing GUI feature (#3848).
    """
    service = window.service
    resolve = getattr(service, 'owned_library_download_target', None)
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
    # A target is the generic Core contract: selection already required the
    # exact account/session plus the complete selected-delivery ABI. Do not
    # repeat that admission as a provider allowlist in CoreUI.
    from ..core.selected_source_offer import SourceOfferTarget
    if type(target) is not SourceOfferTarget:
        return
    provider = target.provider_id
    if (type(provider) is not str or not provider or len(provider) > 96
            or any(char in provider for char in '\x00\r\n')):
        return
    # Presentation belongs to the registered provider descriptor, not a
    # CoreUI mapping. A display name never supplies action admission.
    label = provider.capitalize()
    display_name = getattr(window.service, 'provider_display_name', None)
    if callable(display_name):
        try:
            candidate = display_name(provider)
            if (type(candidate) is str and 0 < len(candidate.strip()) <= 80
                    and all(char.isprintable() for char in candidate)):
                label = candidate.strip()
        except Exception:
            pass
    button = window.action_button(f'Télécharger ce livre {label}', 'folder-download-symbolic')
    button._cifereca_action_id = 'download-owned-' + provider
    button.set_tooltip_text(
        'Télécharger uniquement ce livre possédé, pour son compte et son identité. Aucun lot global.'
    )
    failure = 'Téléchargement ciblé indisponible. Actualisez les droits et vérifiez les fichiers locaux.'
    from ..core.source_context import source_context_epoch
    generation = getattr(window, '_identity_context_generation', None)
    try:
        source_epoch = source_context_epoch(service)
    except ValueError:
        # A malformed Core source generation is not an acquisition grant.
        return

    def current():
        if (getattr(window, '_closed', False)
                or window.service is not service
                or getattr(service.current_identity, 'identity_id', None) != target.identity_id
                or getattr(window, '_identity_context_generation', None) != generation
                or getattr(button, '_cifereca_action_context_revoked', False)):
            return False
        try:
            return source_context_epoch(service) == source_epoch
        except ValueError:
            return False

    def clicked(_button):
        if not current():
            return
        root = getattr(_button, 'get_root', None)
        if callable(root) and root() is None:
            return
        mapped = getattr(_button, 'get_mapped', None)
        if callable(mapped) and not mapped():
            return
        # Convert the passive target at the click, while its navigation
        # context is still current. The Core source lease, not a widget or
        # the selected profile, is then the execution authority.
        try:
            admitted = service.capture_admitted_owned_library_offer(target)
            obtain = service.obtain_admitted_owned_library_offer
            if (type(admitted) is not SourceOfferTarget
                    or admitted != target or admitted.session is not target.session
                    or admitted.selection_epoch is not None
                    or admitted.source_epoch != source_epoch or not current()):
                return
        except Exception:
            if current():
                window.show_error(failure, 'Téléchargement du livre possédé')
            return

        def work():
            try:
                if source_context_epoch(service) != source_epoch:
                    raise ValueError("selected_library_source_revoked")
            except Exception:
                raise RuntimeError('Le contexte du téléchargement a changé.') from None
            try:
                return obtain(admitted)
            except Cancelled:
                raise
            except Exception:
                raise RuntimeError(failure) from None
        def done(result):
            if current() and getattr(result, 'path', None) is not None:
                window._activity_result('Livre téléchargé dans la Bibliothèque.')
                window.refresh_library(False)
                window.refresh_recent()
                from .search_snapshot_refresh import invalidate_search_snapshot
                invalidate_search_snapshot(window)
        def failed(_exc):
            if current():
                window.show_error(failure, 'Téléchargement du livre possédé')
        window.runner.submit(
            f'Téléchargement du livre {label} possédé…',
            work, done, failed, control=_button, priority=TaskPriority.USER_ACTION,
            kind='obtain', origin='user',
        )
    button.connect('clicked', clicked)
    detail.add_actions([button])


def add_local_book_actions(window, detail, item, *, Gtk, Gio, ArtifactRouteRequest, TaskPriority, identity_has_attested_possession):
    if getattr(window, '_closed', False):
        return
    # A foreign library row is not a borrower capability of the selected
    # identity. Never resolve its owned-download action through B's context.
    active_owner = getattr(
        getattr(window.service, 'current_identity', None), 'identity_id', None,
    )
    foreign_owner = item.get('identity_id')
    if (type(foreign_owner) is str and foreign_owner
            and foreign_owner != active_owner):
        from .foreign_placement_send_controller import (
            add_foreign_present_placement_send,
        )
        add_foreign_present_placement_send(
            window, detail, item, TaskPriority=TaskPriority,
        )
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
    # A Search/Recommendations detail action may outlive its GTK selection,
    # the active profile or a provider disconnect/reconnect (A -> B -> A).
    # Capture both identity and Core source leases before creating callbacks.
    from ..core.source_context import source_context_epoch
    # Pin the source service as part of this specific local physical action.
    # A widget's later context is only a projection/click guard.
    service = window.service
    generation = getattr(window, '_identity_context_generation', None)
    try:
        source_epoch = source_context_epoch(window.service)
    except ValueError:
        return
    def context_current():
        if (getattr(window, '_closed', False)
                or window.service is not service
                or getattr(getattr(window.service, 'current_identity', None),
                           'identity_id', None) != identity_id
                or getattr(window, '_identity_context_generation', None)
                    != generation):
            return False
        try:
            return source_context_epoch(window.service) == source_epoch
        except ValueError:
            return False
    def action_current(button, *, gtk=False):
        # DetailPanel revokes old widgets before replacing the current book.
        # A retained Python callback cannot use that stale file, even when
        # the identity and source epoch themselves have not changed.
        if (not context_current()
                or getattr(button, '_cifereca_action_context_revoked', False)):
            return False
        if gtk:
            # GTK state is only read from click callbacks, never workers.
            root = getattr(button, 'get_root', None)
            mapped = getattr(button, 'get_mapped', None)
            if (callable(root) and root() is None) or (
                    callable(mapped) and not mapped()):
                return False
        return True

    if not context_current():
        return
    try:local_path = window.service.local_book_path(placement_id,identity_id=identity_id)
    except Exception:
        window._add_catalogue_action(detail, item); return
    opening=window.action_button('Ouvrir','document-open-symbolic')
    opening._cifereca_action_id = 'open-local'
    def open_book(_button):
        if not action_current(_button, gtk=True):
            return
        try:
            # Recheck placement and widget lease before launching an old action.
            uri=window.service.local_book_path(placement_id,identity_id=identity_id).as_uri()
            if not action_current(_button, gtk=True):
                return
            Gio.AppInfo.launch_default_for_uri(uri,None)
        except Exception:
            if not getattr(window, '_closed', False):
                # Launchers may return private file paths or command details.
                window.show_error('Ouverture locale indisponible', 'Ouverture du livre impossible')
    opening.connect('clicked',open_book)
    buttons=[opening]
    if getattr(local_path, "suffix", "").lower() in (".epub", ".kepub"):
        from ..core.notice_epub_witness import local_epub_witness, EpubWitnessProposal, EpubWitnessReview
        from ..core.notice_parse_witness_store import ApprovedParseWitness
        from ..core.notice_parse_witness import ParseWitnessReview
        from ..core.search_orchestration import capture_search_read_scope
        witness = window.action_button("Examiner le témoin EPUB", "document-properties-symbolic")
        witness._cifereca_action_id = "review-local-epub-witness"
        proposal = None
        busy = False
        revalidate = False

        def witness_click(_button):
            nonlocal busy, proposal, revalidate
            if busy or not action_current(witness, gtk=True):
                return
            try:
                scope = capture_search_read_scope(service, identity_id)
            except ValueError:
                return
            busy = True
            accepted = proposal
            renewal = revalidate

            def work():
                return local_epub_witness(
                    service, placement_id=placement_id, scope=scope,
                    approved=accepted, revalidate=renewal,
                    current=lambda: action_current(witness),
                )

            def done(result):
                nonlocal busy, proposal, revalidate
                busy = False
                if not action_current(witness, gtk=True):
                    return
                proposal = result if type(result) is EpubWitnessProposal else None
                revalidate = (proposal.revalidation if proposal is not None else
                              type(result) is EpubWitnessReview and result.can_revalidate)
                if proposal is not None:
                    witness.set_label(("Revalider" if revalidate else "Valider") + " le témoin EPUB : titre"
                                      + (" et auteurs" if proposal.authors else ""))
                    witness.set_tooltip_text(proposal.title + "\n" + ", ".join(proposal.authors))
                else:
                    witness.set_label("Examiner une nouvelle baseline EPUB" if revalidate
                                      else "Examiner le témoin EPUB")
                    witness.set_tooltip_text("Rejouer le parseur local sans réseau")
                    if type(result) is ApprovedParseWitness:
                        window.toast("Témoin EPUB validé.")
                    elif type(result) is EpubWitnessReview:
                        labels = {"partial": "archive partielle", "complete": "archive créée",
                                  "failed": "échec archive", "capacity_refused": "capacité atteinte",
                                  "pending": "capture en attente"}
                        window.toast("Parsing : régression possible · " +
                                     labels.get(result.capture_state, "capture indisponible"))
                    elif type(result) is ParseWitnessReview:
                        window.toast("Parsing : régression possible" if result.affected_fields
                                     else "Témoin EPUB contrôlé.")
                    else:
                        window.toast("Témoin EPUB indisponible (fichier ou contexte non admissible).")
            window.runner.submit("Contrôle du témoin EPUB…", work, done,
                                 lambda _error: done(None), priority=TaskPriority.METADATA,
                                 kind="detail-cache", origin="user")
        witness.connect("clicked", witness_click)
        buttons.append(witness)
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
    # The Core source/placement is already verified above. Build an exact
    # destination list before creating any action widget; do not turn titles
    # or similar editions into evidence that another profile owns this book.
    eligible = []
    for target in window.service.identities:
        if target.identity_id == identity_id:
            continue
        if source_item_id and identity_has_attested_possession(
            known_possessions,
            identity_id=target.identity_id,
            provider_id=source_provider,
            source_item_id=source_item_id,
            credential_id=source_credential,
        ):
            continue
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
        eligible.append(target)

    # The selected source and the destination are never reinterpreted from
    # the global navigation selector when the task eventually executes.
    # The Core worker revalidates physical bytes, identity and provenance.
    sending = [False]

    def queue_send(button, target_id, *, parent=None):
        if sending[0] or not action_current(button, gtk=True):
            return
        if parent is not None and not action_current(parent, gtk=True):
            return
        sending[0] = True

        def still_current():
            return (action_current(button)
                    and (parent is None or action_current(parent)))

        def work():
            # The user already clicked the exact A placement and B recipient.
            # No visual GTK selection/button generation may cancel a queued
            # physical copy. Core rechecks ownership and bytes under its
            # acquisition lock; source removal/replacement remains blocking.
            service.local_book_path(placement_id, identity_id=identity_id)
            return service.materialize_to_identity(placement_id, target_id)

        def done(result):
            sending[0] = False
            if still_current():
                status = result.get('status') if type(result) is dict else None
                if (type(status) is str and status in ('complete', 'unchanged')
                        and type(result.get('target_identity_id')) is str
                        and result['target_identity_id'] == target_id
                        and type(result.get('placement_id')) is str
                        and result['placement_id']
                        and type(result.get('path')) is str
                        and result['path'] and chr(0) not in result['path']):
                    window._activity_result(
                        'Livre déjà présent dans ce profil'
                        if status == 'unchanged' else 'Livre envoyé'
                    )
                    window.refresh_recent()
                    window.refresh_library(False)
                else:
                    window.show_error(
                        'Copie locale non confirmée ; vérifiez les fichiers.',
                        'Envoi du livre impossible',
                    )

        def failed(exc):
            sending[0] = False
            if still_current() and not isinstance(exc, Cancelled):
                # Do not reflect a potentially private exception/path into UI.
                window.show_error(
                    'Envoi local indisponible ; vérifiez les fichiers et le profil.',
                    'Envoi du livre impossible',
                )

        try:
            window.runner.submit(
                'Envoi du fichier local…', work, done, failed,
                control=button, priority=TaskPriority.USER_ACTION,
                kind='materialize',
            )
        except Exception:
            sending[0] = False
            raise

    from .library_usb_intents import build_library_usb_intents
    usb = build_library_usb_intents(
        window, detail, Gtk=Gtk,
        placement_id=placement_id, identity_id=identity_id,
    )
    if usb is not None:
        choices = usb._cifereca_send_choices
        for target in eligible:
            choice = Gtk.Button(label=str(target.label))
            choice.add_css_class("flat")
            choice._cifereca_action_id = "send-local:" + target.identity_id
            choice.connect("clicked", lambda clicked, target_id=target.identity_id:
                           queue_send(clicked, target_id, parent=usb))
            choices.append(choice)
        buttons.append(usb)
    elif len(eligible) == 1:
        target = eligible[0]
        button = window.action_button(
            'Envoyer à ' + target.label, 'document-send-symbolic',
        )
        button._cifereca_action_id = 'send-local:' + target.identity_id
        button.connect(
            'clicked',
            lambda clicked, target_id=target.identity_id:
                queue_send(clicked, target_id),
        )
        buttons.append(button)
    elif len(eligible) > 1:
        # Exactly ONE action in the Detail toolbar. Choices live in a GTK
        # popover so keyboard/assistive technology can enumerate recipients.
        menu = Gtk.MenuButton(label='Envoyer à')
        menu._cifereca_action_id = 'send-local-menu'
        menu.set_tooltip_text('Choisir le profil auquel envoyer ce livre')
        popover = Gtk.Popover()
        choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        choices.set_margin_top(8)
        choices.set_margin_bottom(8)
        choices.set_margin_start(8)
        choices.set_margin_end(8)
        for target in eligible:
            choice = Gtk.Button(label=str(target.label))
            choice.add_css_class('flat')
            choice._cifereca_action_id = 'send-local:' + target.identity_id
            choice.set_tooltip_text('Envoyer à ' + str(target.label))
            choice.connect(
                'clicked',
                lambda clicked, target_id=target.identity_id:
                    queue_send(clicked, target_id, parent=menu),
            )
            choices.append(choice)
        popover.set_child(choices)
        menu.set_popover(popover)
        buttons.append(menu)
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
            menu = usb if usb is not None else Gtk.MenuButton(label='Envoyer vers…')
            if usb is None:
                menu._cifereca_action_id = 'artifact-routing'
                menu.set_tooltip_text('Envoyer cet artefact vers une cible de stockage compatible')
                popover = Gtk.Popover()
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                box.set_margin_top(8); box.set_margin_bottom(8); box.set_margin_start(8); box.set_margin_end(8)
            else:
                box = usb._cifereca_send_choices
            for target in route_targets:
                target_button = Gtk.Button(label=str(target.target.label or target.target.target_id))
                target_button.add_css_class('flat')
                def route(_button, selected=target):
                    if (not action_current(menu, gtk=True)
                            or not action_current(_button, gtk=True)):
                        return
                    request = ArtifactRouteRequest(
                        context.artifact, target_id=selected.target.target_id,
                        identity_id=identity_id,
                    )
                    try:
                        # The Core owns the admitted worker, pinned to this
                        # placement and storage target, not the GTK selection.
                        work = routing.admit_transfer(request, selected)
                        if not callable(work) or not action_current(_button, gtk=True):
                            return
                    except Exception:
                        if action_current(_button):
                            window.show_error('Envoi vers le stockage indisponible', 'Envoi impossible')
                        return
                    def failed(exc):
                        if not action_current(menu) or not action_current(_button):
                            return
                        # Only explicit user-facing errors have a safe message.
                        message = (exc.user_message if isinstance(exc, UserFacingError)
                                   else 'Envoi vers le stockage indisponible')
                        window.show_error(message, 'Envoi impossible')
                    def done(_result):
                        if action_current(menu) and action_current(_button):
                            window._activity_result('Livre envoyé')
                            window.refresh_library(False)
                            window.refresh_recent()
                    window.runner.submit('Envoi vers le stockage…', work,
                        done,
                        failed, control=_button,
                        priority=TaskPriority.USER_ACTION, kind='artifact-route')
                target_button.connect('clicked', route); box.append(target_button)
            if usb is None:
                popover.set_child(box); menu.set_popover(popover); buttons.append(menu)
    detail.add_actions(buttons)
