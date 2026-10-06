from pathlib import Path


def test_entry_row_does_not_use_set_subtitle():
    source = (Path(__file__).parents[1] / "src/cifereca/gui/main_window.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    entry_vars = set()
    for line in lines:
        if "= Adw.EntryRow()" in line or "= Adw.PasswordEntryRow()" in line:
            lhs = line.split("=", 1)[0].strip().split(";")[-1].strip()
            if lhs.isidentifier():
                entry_vars.add(lhs)
    offenders = []
    for lineno, line in enumerate(lines, start=1):
        for name in entry_vars:
            if f"{name}.set_subtitle(" in line:
                offenders.append((lineno, line.strip()))
    assert not offenders, f"EntryRow.set_subtitle is not portable across target libadwaita versions: {offenders}"
