#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = platform.system().lower().startswith("win")


def command_for(action: str) -> list[str]:
    if action == "install":
        return ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "setup-local-kit.ps1")] if IS_WINDOWS else ["bash", str(ROOT / "scripts" / "setup-local-kit.sh")]
    if action == "start":
        return ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "start-dashboard.ps1")] if IS_WINDOWS else ["bash", str(ROOT / "scripts" / "start-dashboard.sh")]
    if action == "test":
        if IS_WINDOWS:
            return [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "pytest", "-q"]
        return ["bash", "-lc", ". .venv/bin/activate && pytest -q"]
    raise ValueError(action)


def run_command(cmd: list[str]) -> int:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    process = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
    code = process.wait()
    print(f"\nProcess exited with code {code}.", flush=True)
    return code


def run_cli_menu() -> int:
    actions = {
        "1": ("Install / Update", "install"),
        "2": ("Start Browser App", "start"),
        "3": ("Run Tests", "test"),
        "q": ("Quit", "quit"),
    }
    print("Local Payment Search Kit")
    print("No graphical toolkit is available, so the launcher is using a terminal menu.")
    print("Merchant credentials are entered only in the local browser setup wizard.")
    while True:
        print("\nChoose an action:")
        for key, (label, _) in actions.items():
            print(f"  {key}) {label}")
        choice = input("> ").strip().lower()
        if choice in ("q", "quit", "exit"):
            return 0
        selected = actions.get(choice)
        if not selected:
            print("Unknown choice. Choose 1, 2, 3, or q.")
            continue
        _, action = selected
        if action == "quit":
            return 0
        return run_command(command_for(action))


def run_gui() -> int:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    class Launcher(tk.Tk):
        def __init__(self) -> None:
            super().__init__()
            self.title("Local Payment Search Kit")
            self.geometry("760x520")
            self.configure(padx=16, pady=16)
            tk.Label(self, text="Local Payment Search Kit", font=("Arial", 18, "bold")).pack(anchor="w")
            tk.Label(
                self,
                text="Choose an action. Install prepares the local environment. Start opens the browser app. Test runs the verification suite.",
                wraplength=700,
                justify="left",
            ).pack(anchor="w", pady=(4, 12))
            buttons = tk.Frame(self)
            buttons.pack(anchor="w", pady=(0, 12))
            tk.Button(buttons, text="Install / Update", command=lambda: self.run_action("install"), width=18).pack(side="left", padx=(0, 8))
            tk.Button(buttons, text="Start Browser App", command=lambda: self.run_action("start"), width=18).pack(side="left", padx=(0, 8))
            tk.Button(buttons, text="Run Tests", command=lambda: self.run_action("test"), width=18).pack(side="left", padx=(0, 8))
            self.output = scrolledtext.ScrolledText(self, wrap="word", height=22)
            self.output.pack(fill="both", expand=True)
            self.write("Ready. No API keys are requested by this launcher. Merchant credentials are entered only in the local browser setup wizard.\n")

        def write(self, text: str) -> None:
            self.output.insert("end", text)
            self.output.see("end")
            self.update_idletasks()

        def run_action(self, action: str) -> None:
            try:
                cmd = command_for(action)
            except Exception as exc:
                messagebox.showerror("Launcher error", str(exc))
                return
            self.write(f"\n$ {' '.join(cmd)}\n")
            threading.Thread(target=self._run_command, args=(cmd,), daemon=True).start()

        def _run_command(self, cmd: list[str]) -> None:
            try:
                process = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
                assert process.stdout is not None
                for line in process.stdout:
                    self.after(0, self.write, line)
                code = process.wait()
                self.after(0, self.write, f"\nProcess exited with code {code}.\n")
            except FileNotFoundError as exc:
                self.after(0, self.write, f"Launcher could not find a required command: {exc}\n")
            except Exception as exc:
                self.after(0, self.write, f"Launcher error: {exc}\n")

    app = Launcher()
    app.mainloop()
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        action = sys.argv[1].strip().lower().removeprefix("--")
        if action in {"install", "start", "test"}:
            return run_command(command_for(action))
    try:
        return run_gui()
    except (ModuleNotFoundError, ImportError) as exc:
        if "tkinter" not in str(exc).lower():
            raise
        return run_cli_menu()
    except Exception as exc:
        if "display" not in str(exc).lower() and "tk" not in exc.__class__.__name__.lower():
            raise
        return run_cli_menu()


if __name__ == "__main__":
    raise SystemExit(main())
