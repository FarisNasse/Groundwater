import argparse
import json
import logging
from pathlib import Path
import sys

from groundwater.config import Config
from groundwater.pipeline import run, write_json
from groundwater.usgs import USGSClient, utc_now


def bbox_arg(value):
    try:
        west, south, east, north = map(float, value.split(","))
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("bbox must be west,south,east,north in decimal degrees") from exc
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="USGS groundwater data pipeline and model comparison")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "replay", "demo"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, help="TOML configuration; otherwise built-in defaults")
        command.add_argument("--root", type=Path, default=Path("artifacts"))
        if name == "replay":
            command.add_argument("--file", type=Path, required=True)
    discover = commands.add_parser("discover", help="Find wells with daily mean groundwater depths")
    discover.add_argument("--bbox", type=bbox_arg, required=True)
    discover.add_argument("--root", type=Path, default=Path("artifacts"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "discover":
            features, pages = USGSClient().discover(args.bbox, args.root / "bronze" / "discovery")
            candidates = []
            for f in features:
                p = f["properties"]
                candidates.append({"site": p.get("monitoring_location_id"),
                                   "series_id": p.get("time_series_id", p.get("timeseries_id")),
                                   "latest_date": p.get("time"), "unit": p.get("unit_of_measure"),
                                   "coordinates": (f.get("geometry") or {}).get("coordinates")})
            args.root.mkdir(parents=True, exist_ok=True)
            write_json(args.root / "discovery.json", {"bbox": args.bbox, "retrieved_at": utc_now(),
                                                       "candidates": candidates, "pages": pages})
            print(json.dumps(candidates, indent=2))
        else:
            config = Config.load(args.config) if args.config else Config()
            mode = {"run": "live", "replay": "replay", "demo": "synthetic"}[args.command]
            output = run(config, args.root, mode, getattr(args, "file", None))
            print(f"Completed: {output}\nOpen: {output / 'report.html'}")
    except (ValueError, RuntimeError, OSError) as exc:
        logging.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
