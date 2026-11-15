"""PyInstaller entry shim (the package uses relative imports)."""

from app.__main__ import main

if __name__ == "__main__":
    main()
