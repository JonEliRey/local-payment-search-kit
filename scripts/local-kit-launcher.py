#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext

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
        env = os.environ.copy()
        try:
            process = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            assert process.stdout is not None
            for line in process.stdout:
                self.after(0, self.write, line)
            code = process.wait()
            self.after(0, self.write, f"\nProcess exited with code {code}.\n")
        except FileNotFoundError as exc:
            self.after(0, self.write, f"Launcher could not find a required command: {exc}\n")
        except Exception as exc:
            self.after(0, self.write, f"Launcher error: {exc}\n")


def main() -> int:
    app = Launcher()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
