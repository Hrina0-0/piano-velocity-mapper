"""查一下打包进去的第三方依赖的许可（分发前心里有数）。"""
from importlib.metadata import metadata

for pkg in ("pygame", "mido"):
    try:
        m = metadata(pkg)
        print(pkg, "version =", m.get("Version"))
        lic = m.get("License") or ""
        print("   License:", lic[:160].replace("\n", " "))
        for c in (m.get("Classifier") or []):
            if "License" in c:
                print("   ", c)
        home = m.get("Home-page") or ""
        print("   Home:", home)
    except Exception as exc:
        print(pkg, "ERR", exc)
    print()
