"""Build dist/photofootprint-<version>.zip for QGIS "Install from ZIP".

Usage: python3 scripts/package.py
"""

import configparser
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "photofootprint")


def main():
    meta = configparser.ConfigParser()
    meta.read(os.path.join(PLUGIN, "metadata.txt"))
    version = meta["general"]["version"]
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", f"photofootprint-{version}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(PLUGIN):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                if name.endswith(".pyc"):
                    continue
                full = os.path.join(root, name)
                z.write(full, os.path.relpath(full, ROOT))
        z.write(os.path.join(ROOT, "LICENSE"), "photofootprint/LICENSE")
    print(out)


if __name__ == "__main__":
    main()
