# PyInstaller recipe for Marquee's Windows download (run: pyinstaller packaging/marquee.spec).
# One file, dist/Marquee.exe, double-clicked to start. Its data goes to %LOCALAPPDATA%\Marquee
# (sources.py), since a one-file .exe unpacks to a new temporary folder on every start.
#
# Linux has no .exe: its download is the source folder with run.sh and install.sh, run on the
# system's own Python, GTK and WebKit.
from pathlib import Path

root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "marquee.py")],
    pathex=[str(root)],
    datas=[(str(root / name), ".") for name in ("ui.html", "theme.json", "icon.svg")],
    hiddenimports=["webwindow", "tzdata"],
    excludes=["tkinter", "gi", "cairo"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="Marquee", console=False, upx=False,
          icon=str(root / "packaging" / "marquee.ico"))
