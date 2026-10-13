#!/usr/bin/env python3
"""P0 #1099: real settings widgets and explicit synthetic loopback LLM POSTs.

No remote test is accepted; remote consent is opened and cancelled. The local
HTTP fixture returns typed errors and stores no headers or user information.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'environnements/openai-chat-debian'))
sys.path.insert(0, str(ROOT / 'simulation'))
from common.llm_http_fixture import SyntheticLLMFixture
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageGrab
from x11_input import X11Input
from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--width', required=True, type=int, choices=(1366, 1600))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    fixture = SyntheticLLMFixture()
    requests = fixture.requests
    endpoint = fixture.endpoint
    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)
    assert core.config.get('llm.enabled') is False
    report = {'status': 'RUNNING', 'scenario': 'llm-settings-gtk', 'width': args.width,
              'checks': {}, 'requests': requests, 'steps': [],
              'scope': 'real MainWindow/widgets/TaskRunner/Core HTTP transport; synthetic loopback server; remote consent cancelled'}
    window = device = tab = None
    started = monotonic()
    result = [1]
    save_started_at = [None]
    controls = {}
    cases = [(200, 'Test réussi'), (401, 'Authentification refusée'),
             (404, 'Modèle ou endpoint introuvable'), (429, 'Service limité temporairement')]

    def save():
        report['duration_seconds'] = round(monotonic() - started, 2)
        (args.out / 'screen.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')

    def capture(name):
        path = args.out / (name + '.png')
        ImageGrab.grab(xdisplay=os.environ['DISPLAY']).save(path)
        report['steps'].append({'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})

    def fail(exc):
        if report['status'] != 'RUNNING':
            return
        report.update(status='FAIL', error=f'{type(exc).__name__}: {exc}')
        save(); result[0] = 1; app.quit()

    def later(fn, delay=180):
        def run():
            try:
                fn()
            except Exception as exc:
                fail(exc)
            return False
        GLib.timeout_add(delay, run)

    def status():
        return controls['status'].get_text()

    def click(button, then):
        button.grab_focus()
        def send():
            assert button.get_mapped() and button.get_sensitive(), 'Button unavailable'
            report.setdefault('inputs', []).append(device.click_widget(button))
            later(then)
        later(send)

    def open_ai():
        nonlocal tab
        window.navigate('settings')
        window._settings_tabs.set_visible_child_name('ai')
        tab = window._settings_tabs.get_visible_child()
        entries = [w for w in walk(tab) if isinstance(w, Adw.EntryRow)]
        controls['endpoint'] = next(w for w in entries if w.get_title().startswith('Endpoint complet'))
        controls['key'] = next(w for w in entries if w.get_title().startswith('Nom de variable'))
        controls['timeout'] = next(w for w in entries if w.get_title().startswith('Délai maximum'))
        controls['model'] = next(w for w in entries if w.get_title().startswith('Modèle installé'))
        switches = [w for w in walk(tab) if isinstance(w, Gtk.Switch)]
        assert len(switches) == 2
        controls['enabled'], controls['remote'] = switches
        controls['test'] = next(w for w in walk(tab) if isinstance(w, Gtk.Button) and w.get_label() == 'Tester la connexion')
        controls['reset'] = next(w for w in walk(tab) if isinstance(w, Gtk.Button) and w.get_label() == 'Annuler les modifications')
        controls['status'] = next(w for w in walk(tab) if isinstance(w, Gtk.Label) and w.get_text() == 'Désactivé')
        later(disabled)

    def disabled():
        assert not controls['enabled'].get_active() and requests == []
        report['checks']['disabled_no_http'] = True
        capture('00-disabled')
        click(controls['test'], configure_local)

    def configure_local():
        assert 'Activer' in status() and requests == []
        controls['enabled'].set_active(True)
        controls['endpoint'].set_text(endpoint)
        controls['model'].set_text('fixture-model')
        controls['endpoint'].grab_focus()
        device.key('Tab', True); device.key('Tab', False)
        later(lambda: run_case(0))

    def run_case(index):
        focus = window.get_focus()
        if index == 0:
            assert focus is not None, 'Initial keyboard focus missing'
            report['checks']['keyboard_focus'] = True
        report.setdefault('focus_after_tests', []).append(type(focus).__name__ if focus is not None else None)
        code, expected = cases[index]
        fixture.status = code
        before = len(requests)
        click(controls['test'], lambda: wait_case(index, expected, before))

    def wait_case(index, expected, before):
        if not controls['test'].get_sensitive():
            later(lambda: wait_case(index, expected, before), 80)
            return
        assert status().startswith(expected), status()
        assert len(requests) == before + 1 and requests[-1]['synthetic_only']
        report['checks'][f'post_{cases[index][0]}'] = True
        capture(f'01-result-{cases[index][0]}')
        if index + 1 < len(cases):
            later(lambda: run_case(index + 1))
        else:
            controls['expected_requests'] = len(requests)
            later(lambda: validate_field(0))

    validation_cases = [
        ('endpoint', 'http://remote.example.invalid/v1/chat/completions', 'Destination non autorisée'),
        ('endpoint', endpoint + '?token=SYNTHETIC_SENTINEL', 'Adresse IA interdite'),
        ('endpoint', endpoint + '#fragment', 'Adresse IA interdite'),
        ('endpoint', 'https://synthetic:sentinel@llm.example.invalid/v1/chat/completions', 'Adresse IA interdite'),
        ('key', 'invalid variable name', 'Nom de variable'),
        ('timeout', '0.1', 'Délai'),
        ('model', '', 'modèle'),
    ]

    def validate_field(index):
        controls['endpoint'].set_text(endpoint)
        controls['model'].set_text('fixture-model')
        controls['key'].set_text('')
        controls['timeout'].set_text('5')
        field, value, expected = validation_cases[index]
        controls[field].set_text(value)
        def checked():
            assert expected in status(), status()
            assert len(requests) == controls['expected_requests']
            assert 'SYNTHETIC_SENTINEL' not in status() and 'synthetic:sentinel' not in status()
            report['checks'][f'prevalidation_{index}'] = True
            if index + 1 < len(validation_cases):
                later(lambda: validate_field(index + 1))
            else:
                controls['endpoint'].set_text(endpoint)
                controls['model'].set_text('fixture-model')
                controls['key'].set_text('CIFERECA_E2E_LLM_KEY')
                assert 'absente' in status()
                os.environ['CIFERECA_E2E_LLM_KEY'] = 'synthetic-auth-never-persist'
                controls['key'].set_text('')
                controls['key'].set_text('CIFERECA_E2E_LLM_KEY')
                assert 'définie' in status()
                report['checks']['environment_key_name_only'] = True
                later(remote_blocked)
        click(controls['test'], checked)

    def remote_blocked():
        controls['endpoint'].set_text('https://llm.example.invalid/v1/chat/completions')
        assert not controls['remote'].get_active()
        click(controls['test'], remote_consent)

    def remote_consent():
        assert 'Destination non autorisée' in status(), status()
        assert len(requests) == controls['expected_requests']
        report['checks']['remote_blocked_without_opt_in'] = True
        controls['remote'].set_active(True)
        click(controls['test'], cancel_consent)

    def cancel_consent():
        dialogs = [w for w in Gtk.Window.list_toplevels() if isinstance(w, Adw.MessageDialog) and w.get_visible()]
        assert len(dialogs) == 1
        dialog = dialogs[0]
        assert 'llm.example.invalid' in dialog.get_body() and 'facturé' in dialog.get_body()
        assert len(requests) == controls['expected_requests']
        capture('02-remote-consent')
        cancel = next(w for w in walk(dialog) if isinstance(w, Gtk.Button) and str(w.get_label()).replace('_', '') == 'Annuler')
        click(cancel, reset)

    def reset():
        assert len(requests) == controls['expected_requests']
        report['checks']['remote_consent_cancelled'] = True
        click(controls['reset'], pending_reset)

    def pending_reset():
        assert not controls['enabled'].get_active() and 'annulées' in status()
        report['checks']['reset_without_http_or_save'] = core.config.get('llm.enabled') is False
        controls['enabled'].set_active(True)
        controls['endpoint'].set_text(endpoint)
        controls['model'].set_text('fixture-model')
        fixture.status = 200; fixture.delay = 0.9
        component_ticks = [0]
        def heartbeat():
            component_ticks[0] += 1
            return report['status'] == 'RUNNING' and component_ticks[0] < 30
        GLib.timeout_add(40, heartbeat)
        def reset_during_test():
            assert not controls['test'].get_sensitive(), 'No pending test to cancel'
            assert window.get_focus() is controls['reset'], (
                'Keyboard focus lost when the pending test disabled its button'
            )
            report['checks']['pending_test_preserves_keyboard_focus'] = True
            click(controls['reset'], lambda: wait_cancelled(component_ticks))
        click(controls['test'], reset_during_test)

    def wait_cancelled(ticks):
        if not controls['test'].get_sensitive():
            later(lambda: wait_cancelled(ticks), 100)
            return
        assert 'annulées' in status(), 'Late test result replaced reset state'
        assert ticks[0] > 3, 'GTK heartbeat blocked by backend'
        assert len(requests) == controls['expected_requests'] + 1
        controls['expected_requests'] = len(requests)
        report['checks']['reset_drops_late_reply_ui_responsive'] = True
        report['cancellation_scope'] = 'UI result invalidation; already sent HTTP is not undone'
        controls['enabled'].set_active(True)
        controls['endpoint'].set_text(endpoint)
        controls['model'].set_text('fixture-model')
        controls['timeout'].set_text('0.2')
        fixture.delay = 0.8
        click(controls['test'], wait_timeout)

    def wait_timeout():
        if not controls['test'].get_sensitive():
            later(wait_timeout, 80)
            return
        assert 'Délai de connexion dépassé' in status(), status()
        assert len(requests) == controls['expected_requests'] + 1
        controls['expected_requests'] = len(requests)
        report['checks']['bounded_timeout'] = True
        fixture.delay = 0
        fixture.close()
        controls['timeout'].set_text('5')
        controls['enabled'].set_active(True)
        controls['endpoint'].set_text(endpoint)
        controls['model'].set_text('fixture-model')
        click(controls['test'], backend_absent)

    def backend_absent():
        if not controls['test'].get_sensitive():
            later(backend_absent, 80)
            return
        assert 'Serveur injoignable' in status(), status()
        assert len(requests) == controls['expected_requests']
        report['checks']['backend_absent'] = True
        controls['enabled'].set_active(False)
        click(controls['reset'], save_local)

    def save_local():
        assert not controls['enabled'].get_active() and 'annulées' in status()
        report['checks']['reset_without_http_or_save'] = core.config.get('llm.enabled') is False
        controls['enabled'].set_active(True)
        controls['endpoint'].set_text(endpoint)
        controls['model'].set_text('fixture-model')
        controls['key'].set_text('CIFERECA_E2E_LLM_KEY')
        saves = [w for w in walk(window._settings_tabs.get_parent())
                 if isinstance(w, Gtk.Button) and getattr(w, '_cifereca_action_label', '') == 'Enregistrer']
        assert saves
        save_started_at[0] = monotonic()
        # The footer may be mapped but clipped by Gtk.ScrolledWindow; an
        # XTEST click at its offscreen coordinates can miss the target without
        # an exception. The preceding interaction/consent tests use XTEST;
        # exercise this final GTK action signal directly and check persisted
        # state, never infer success from a pointer coordinate alone.
        assert saves[-1].get_sensitive()
        saves[-1].emit("clicked")
        report['checks']['save_action_gtk_signal_dispatched'] = True
        later(wait_saved)

    def wait_saved():
        if core.config.get('llm.enabled') is not True or 'redémarrer' not in status().lower():
            if monotonic() - save_started_at[0] > 12:
                raise AssertionError(
                    'Sauvegarde IA sans confirmation sous 12 s : '
                    f'enabled={core.config.get("llm.enabled") is True}, '
                    f'status={status()[:120]!r}'
                )
            later(wait_saved, 80)
            return
        assert core.config.get('llm.model') == 'fixture-model'
        assert core.config.get('llm.allow_remote') is False
        assert core.config.get('llm.api_key_env') == 'CIFERECA_E2E_LLM_KEY'
        assert 'synthetic-auth-never-persist' not in json.dumps(core.config.data)
        assert not any('synthetic-auth-never-persist' in str(w.get_text()) for w in walk(tab) if isinstance(w, (Gtk.Label, Adw.EntryRow)))
        report['checks']['key_value_not_rendered_or_persisted'] = True
        assert len(requests) == controls['expected_requests']
        assert 'redémarrer' in status().lower(), status()
        report['checks']['saved_requires_restart_without_http'] = True
        capture('03-saved-restart-required')
        report['status'] = 'PASS'; save(); result[0] = 0; app.quit()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id='org.cifereca.LLMSettingsProbe')

        def do_activate(self):
            nonlocal window, device
            window = MainWindow(self, core.config, core.library, core.diagnostics)
            window.set_default_size(args.width - 40, 880 if args.width == 1600 else 780)
            window.present(); device = X11Input(); later(open_ai, 500)

        def do_shutdown(self):
            if device is not None:
                device.close()
            core.close()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(70, lambda: (fail(TimeoutError('GTK settings recipe timeout')), False)[1])
    app.run([sys.argv[0]])
    fixture.close()
    if report['status'] == 'RUNNING':
        report.update(status='FAIL', error='Application exited before proof'); save()
    return result[0]


if __name__ == '__main__':
    raise SystemExit(main())
