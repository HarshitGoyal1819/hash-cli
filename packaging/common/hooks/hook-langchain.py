# PyInstaller hook for langchain + langchain_core + langchain_ollama
from PyInstaller.utils.hooks import collect_all

for pkg in ("langchain", "langchain_core", "langchain_ollama"):
    d, b, h = collect_all(pkg)
    datas    = locals().get("datas",    []) + d
    binaries = locals().get("binaries", []) + b
    hiddenimports = locals().get("hiddenimports", []) + h
