"""Minimal Tkinter control panel for the mobile E2E pipeline.

Run with::

    python -m mobile_e2e.gui

Fill in the Appium server, an optional proxy, the read/input locators and the
AI agent's role + tone, then press *Run workflow*. The workflow runs on a
background thread; framework logs and the result stream into the log pane. Use
*Preview proxy* to sanity-check a proxy string without needing a device.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk

from mobile_e2e.core.exceptions import ProxyParseError
from mobile_e2e.gui.controller import (
    STRATEGIES,
    GuiController,
    WorkflowRequest,
    parse_proxy_preview,
)

_STRATEGY_LABELS = list(STRATEGIES.keys())
_DEFAULT_STRATEGY = "ID"


class _QueueLogHandler(logging.Handler):
    """A logging handler that forwards formatted records to a queue."""

    def __init__(self, sink: "queue.Queue[str]") -> None:
        super().__init__()
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.put(self.format(record))


class App(tk.Tk):
    """The main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Mobile E2E — Control Panel")
        self.geometry("640x760")
        self.minsize(560, 640)

        self._controller = GuiController()
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._running = False

        self._build_form()
        self._build_log()
        self.after(100, self._drain_log)

    # -- layout -------------------------------------------------------------
    def _build_form(self) -> None:
        pad = {"padx": 6, "pady": 3}
        container = ttk.Frame(self)
        container.pack(fill="x", padx=8, pady=6)

        # Appium / session
        session = ttk.LabelFrame(container, text="Appium session")
        session.pack(fill="x", **pad)
        self.server_url = self._row(session, "Server URL", "http://127.0.0.1:4723")
        self.device = self._row(session, "Device", "emulator-5554")
        self.platform = tk.StringVar(value="Android")
        prow = ttk.Frame(session)
        prow.pack(fill="x", **pad)
        ttk.Label(prow, text="Platform", width=16).pack(side="left")
        ttk.Combobox(
            prow, textvariable=self.platform, values=["Android", "iOS"],
            state="readonly", width=18,
        ).pack(side="left")

        # Proxy
        proxy = ttk.LabelFrame(container, text="Proxy (IP:Port:Login:Password)")
        proxy.pack(fill="x", **pad)
        self.proxy_string = self._row(proxy, "Proxy", "")
        ttk.Button(proxy, text="Preview proxy", command=self._on_preview_proxy).pack(
            anchor="e", padx=6, pady=3
        )

        # Locators
        loc = ttk.LabelFrame(container, text="Locators")
        loc.pack(fill="x", **pad)
        self.read_strategy, self.read_value = self._locator_row(loc, "Read")
        self.input_strategy, self.input_value = self._locator_row(loc, "Input")
        self.submit_strategy, self.submit_value = self._locator_row(
            loc, "Submit (optional)"
        )

        # AI agent
        ai = ttk.LabelFrame(container, text="AI agent")
        ai.pack(fill="x", **pad)
        self.system_prompt = self._text_row(ai, "System prompt")
        self.tone = self._row(ai, "Tone of voice", "")
        self.fallback = self._row(ai, "Fallback text", "")
        self.model = self._row(ai, "Model", "gpt-4o-mini")
        self.base_url = self._row(ai, "Base URL (local LLM)", "")

        # Actions
        actions = ttk.Frame(container)
        actions.pack(fill="x", **pad)
        self.run_button = ttk.Button(
            actions, text="Run workflow", command=self._on_run
        )
        self.run_button.pack(side="left")
        ttk.Button(actions, text="Clear log", command=self._clear_log).pack(
            side="left", padx=6
        )
        self.status = ttk.Label(actions, text="Idle")
        self.status.pack(side="right")

    def _build_log(self) -> None:
        frame = ttk.LabelFrame(self, text="Log")
        frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.log = tk.Text(frame, height=12, wrap="word", state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        scroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scroll.set)

    def _row(self, parent: tk.Widget, label: str, default: str) -> tk.StringVar:
        var = tk.StringVar(value=default)
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=6, pady=3)
        ttk.Label(row, text=label, width=16).pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        return var

    def _text_row(self, parent: tk.Widget, label: str) -> tk.Text:
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=6, pady=3)
        ttk.Label(row, text=label, width=16).pack(side="left", anchor="n")
        widget = tk.Text(row, height=3, wrap="word")
        widget.pack(side="left", fill="x", expand=True)
        return widget

    def _locator_row(self, parent: tk.Widget, label: str):
        strategy = tk.StringVar(value=_DEFAULT_STRATEGY)
        value = tk.StringVar(value="")
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=6, pady=3)
        ttk.Label(row, text=label, width=16).pack(side="left")
        ttk.Combobox(
            row, textvariable=strategy, values=_STRATEGY_LABELS,
            state="readonly", width=18,
        ).pack(side="left")
        ttk.Entry(row, textvariable=value).pack(
            side="left", fill="x", expand=True, padx=(6, 0)
        )
        return strategy, value

    # -- request assembly ---------------------------------------------------
    def _collect(self) -> WorkflowRequest:
        return WorkflowRequest(
            server_url=self.server_url.get(),
            platform=self.platform.get(),
            device=self.device.get(),
            proxy_string=self.proxy_string.get(),
            read_strategy=STRATEGIES[self.read_strategy.get()],
            read_value=self.read_value.get(),
            input_strategy=STRATEGIES[self.input_strategy.get()],
            input_value=self.input_value.get(),
            submit_strategy=STRATEGIES[self.submit_strategy.get()],
            submit_value=self.submit_value.get(),
            system_prompt=self.system_prompt.get("1.0", "end").strip(),
            tone=self.tone.get(),
            fallback=self.fallback.get(),
            model=self.model.get(),
            base_url=self.base_url.get(),
        )

    # -- actions ------------------------------------------------------------
    def _on_preview_proxy(self) -> None:
        try:
            self._append(f"Proxy: {parse_proxy_preview(self.proxy_string.get())}")
        except ProxyParseError as exc:
            self._append(f"Invalid proxy: {exc}")

    def _on_run(self) -> None:
        if self._running:
            return
        try:
            request = self._collect()
            self._controller.validate(request)
        except ValueError as exc:
            self._append(f"Validation error: {exc}")
            return

        self._running = True
        self.run_button.configure(state="disabled")
        self.status.configure(text="Running…")
        self._append("--- starting workflow ---")
        threading.Thread(
            target=self._run_worker, args=(request,), daemon=True
        ).start()

    def _run_worker(self, request: WorkflowRequest) -> None:
        handler = _QueueLogHandler(self._log_queue)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            result = self._controller.run(request)
            if result.ok:
                self._log_queue.put(f"RESULT ok — response: {result.response!r}")
            else:
                self._log_queue.put(
                    f"RESULT failed — {type(result.error).__name__}: {result.error}"
                )
        except Exception as exc:  # noqa: BLE001 - surface anything to the log
            self._log_queue.put(f"ERROR: {type(exc).__name__}: {exc}")
        finally:
            root.removeHandler(handler)
            self._log_queue.put("__DONE__")

    # -- log plumbing -------------------------------------------------------
    def _drain_log(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                if line == "__DONE__":
                    self._running = False
                    self.run_button.configure(state="normal")
                    self.status.configure(text="Idle")
                else:
                    self._append(line)
        except queue.Empty:
            pass
        self.after(100, self._drain_log)

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
