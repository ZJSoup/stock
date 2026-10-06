from __future__ import annotations

import argparse
import asyncio
import pathlib

from ib_insync import util

from .config import default_config_path, load_config


def parse_args():
    p = argparse.ArgumentParser(prog="momo_bot")
    p.add_argument("--config", type=pathlib.Path, default=None)
    p.add_argument("--data-dir", type=pathlib.Path,
                   default=pathlib.Path.home() / ".local" / "share" / "stock-momo")
    p.add_argument("--log-dir", type=pathlib.Path,
                   default=pathlib.Path.home() / "Library" / "Logs" / "stock-momo")
    p.add_argument("--once", action="store_true",
                   help="single cycle, for smoke tests (not implemented for live loop)")
    return p.parse_args()


def main():
    from .app import App

    args = parse_args()
    settings = load_config(args.config or default_config_path())
    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.log_dir.mkdir(parents=True, exist_ok=True)
    util.logToFile(path=str(args.log_dir / "momo.log"), level=20)
    app = App(settings, args.data_dir)
    util.patchAsyncio()
    try:
        util.run(app.run())
    except KeyboardInterrupt:
        pass
    finally:
        util.run(app.stop()) if app.ib.isConnected() else None


if __name__ == "__main__":
    main()
