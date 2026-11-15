# PyInstaller spec: builds a single-file Windows console exe.
# Build from the repo root:  pyinstaller host/remote-presentation.spec
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("app")
    + ["qrcode", "PIL", "PIL.Image", "PIL.ImageDraw"]
)
import sys
if sys.platform == "win32":
    hiddenimports += ["win32com", "win32com.client", "pythoncom", "pywintypes"]

a = Analysis(
    ["run_exe.py"],
    pathex=["."],
    datas=[("../web", "web")],
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="RemotePresentation",
    console=True,  # keep the console: it shows the pairing URL + QR code
    icon="icon.ico",
    upx=False,
)
