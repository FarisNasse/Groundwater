"""One-time environment setup, then open the local Groundwater dashboard."""

import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    if sys.version_info < (3, 11):
        print("Install Python 3.12 or newer, then open Start Dashboard.cmd again.")
        return 1
    project = Path(__file__).resolve().parent
    environment = project / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("First launch: creating a local Python environment…", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project / "src")
    probe = subprocess.run([str(python), "-c", "import numpy, pandas, sklearn, groundwater"],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode:
        print("First launch: installing dependencies. Internet access is needed for this step.", flush=True)
        subprocess.run([str(python), "-m", "ensurepip", "--upgrade"], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-e", str(project)], check=True)
    print("Opening your local workspace…", flush=True)
    return subprocess.call([str(python), "-m", "groundwater", "dashboard", "--project", str(project),
                            "--root", str(project / "artifacts"), *sys.argv[1:]], cwd=project, env=env)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"\nSetup could not finish: {exc}\nCheck your connection and Python installation, then retry.")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)
