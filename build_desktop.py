"""valz desktop build orchestrator.

Builds the Windows onedir bundle via PyInstaller, then zips it for
distribution. Pure Python so the pipeline is identical on any host.

Run:
    .venv-build\\Scripts\\python build_desktop.py

Outputs:
    dist/valz/valz.exe             (the launcher)
    dist/valz/_internal/...        (bundled Python + payload)
    dist/valz-<version>-portable.zip   (ship-this artifact)
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv-build"
PY = VENV / "Scripts" / "python.exe"
SPEC = ROOT / "valz.spec"
DIST = ROOT / "dist"


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd, log_path):
    print("$", " ".join(map(str, cmd)))
    with open(log_path, "w", encoding="utf-8") as logf:
        proc = subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT,
                              check=False)
    if proc.returncode != 0:
        print(f"  exit={proc.returncode}; see {log_path}", file=sys.stderr)
        fail("subprocess failed")
    # print the "Build complete" line so the user has feedback
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "Build complete" in line or "ERROR" in line:
                print("  >", line)


def main():
    if not PY.exists():
        fail(f"build venv missing: {PY}. Create with: "
             f"python -m venv .venv-build && "
             f".venv-build\\Scripts\\python -m pip install -r requirements.txt pyinstaller")
    if not SPEC.exists():
        fail(f"spec missing: {SPEC}")
    payload = ROOT / "desktop" / "payload"
    if not (payload / "valz.db").exists():
        fail(f"payload snapshot missing at {payload / 'valz.db'}; "
             f"see docs on pulling from homeserver")

    if DIST.exists():
        print(f"cleaning {DIST}")
        shutil.rmtree(DIST)

    log = ROOT / "tmp" / "pyinstaller.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    run([str(PY), "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean"],
        log)

    exe = DIST / "valz" / "valz.exe"
    if not exe.exists():
        fail(f"build did not produce {exe}")
    print(f"exe: {exe} ({exe.stat().st_size} bytes)")

    # zip the onedir so the user can extract-and-run anywhere
    version = subprocess.check_output(
        [str(PY), "-c",
         "import importlib.util as u;"
         "spec = u.spec_from_file_location('app', 'app.py');"
         "m = u.module_from_spec(spec); spec.loader.exec_module(m);"
         "print(m.VERSION)"],
        cwd=ROOT, text=True).strip()
    zip_path = DIST / f"valz-{version}-portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    src_dir = DIST / "valz"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src_dir.parent))
    print(f"zip: {zip_path} ({zip_path.stat().st_size} bytes)")
    print("done.")


if __name__ == "__main__":
    main()
