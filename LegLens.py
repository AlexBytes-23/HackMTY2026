from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


ROOT = Path(__file__).resolve().parent


class LegLensApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("LegLens — Forensic Auditor")
        self.geometry("1050x720")
        self.minsize(900, 600)

        self.submission_path = tk.StringVar(
            value=str(ROOT / "demo" / "submission.json")
        )

        self._build_ui()

        default = Path(self.submission_path.get())
        if default.exists():
            self.load_submission(default)

    def _build_ui(self):
        header = ttk.Frame(self, padding=18)
        header.pack(fill="x")

        ttk.Label(
            header,
            text="LegLens",
            font=("Segoe UI", 24, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            header,
            text="AI-assisted forensic auditor — investigate aggressively, accuse conservatively",
            font=("Segoe UI", 11),
        ).pack(anchor="w", pady=(4, 0))

        controls = ttk.Frame(self, padding=(18, 0, 18, 12))
        controls.pack(fill="x")

        ttk.Entry(
            controls,
            textvariable=self.submission_path,
        ).pack(side="left", fill="x", expand=True)

        ttk.Button(
            controls,
            text="Browse JSON",
            command=self.browse_json,
        ).pack(side="left", padx=6)

        ttk.Button(
            controls,
            text="Load",
            command=lambda: self.load_submission(
                Path(self.submission_path.get())
            ),
        ).pack(side="left")

        ttk.Button(
            controls,
            text="Run verified demo",
            command=self.run_verified_demo,
        ).pack(side="left", padx=(12, 0))

        self.summary = ttk.Label(
            self,
            text="No submission loaded.",
            padding=(18, 6),
            font=("Segoe UI", 11, "bold"),
        )
        self.summary.pack(fill="x")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=12)

        findings_tab = ttk.Frame(notebook)
        declined_tab = ttk.Frame(notebook)
        metadata_tab = ttk.Frame(notebook)

        notebook.add(findings_tab, text="Findings")
        notebook.add(declined_tab, text="Not pursued")
        notebook.add(metadata_tab, text="Run metadata")

        self.findings_text = self._text_widget(findings_tab)
        self.declined_text = self._text_widget(declined_tab)
        self.metadata_text = self._text_widget(metadata_tab)

        footer = ttk.Label(
            self,
            text="AI interprets. Python verifies. Evidence decides.",
            padding=12,
            anchor="center",
            font=("Segoe UI", 10, "italic"),
        )
        footer.pack(fill="x")

    def _text_widget(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)

        text = tk.Text(
            frame,
            wrap="word",
            font=("Consolas", 10),
            padx=12,
            pady=12,
        )

        scroll = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=text.yview,
        )

        text.configure(yscrollcommand=scroll.set)

        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        return text

    def browse_json(self):
        selected = filedialog.askopenfilename(
            title="Select submission.json",
            filetypes=[
                ("JSON files", "*.json"),
                ("All files", "*.*"),
            ],
        )

        if selected:
            self.submission_path.set(selected)
            self.load_submission(Path(selected))

    def run_verified_demo(self):
        """
        Executes the already-proven deterministic vertical slice.

        This is deliberately labelled DEMO:
        it starts at the reviewed-case boundary and does NOT pretend
        to execute live Investigator / Challenger / Method Critic LLM calls.
        """

        command = [
            sys.executable,
            "-m",
            "dev_eval.vertical_slice_smoke",
        ]

        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:
            messagebox.showerror(
                "Demo error",
                str(exc),
            )
            return

        if result.returncode != 0:
            messagebox.showerror(
                "Demo failed",
                result.stderr or result.stdout,
            )
            return

        output = ROOT / "demo" / "submission.json"

        if not output.exists():
            messagebox.showerror(
                "Demo failed",
                "The demo completed but submission.json was not produced.",
            )
            return

        self.submission_path.set(str(output))
        self.load_submission(output)

        messagebox.showinfo(
            "Verified demo complete",
            "Verifier → Evidence Gate → Finding → Submission completed successfully.",
        )

    def load_submission(self, path: Path):
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            messagebox.showerror(
                "Cannot load submission",
                str(exc),
            )
            return

        findings = data.get("findings", [])
        leads = data.get("leads_not_pursued", [])
        metadata = data.get("run_metadata", {})

        self.summary.configure(
            text=(
                f"Findings: {len(findings)}     "
                f"Not pursued: {len(leads)}     "
                f"Seed: {data.get('seed', '—')}"
            )
        )

        self._render_findings(findings)
        self._render_leads(leads)

        self._replace(
            self.metadata_text,
            json.dumps(
                metadata,
                indent=2,
                ensure_ascii=False,
            ),
        )

    def _render_findings(self, findings):
        if not findings:
            self._replace(
                self.findings_text,
                "No findings were authorized.\n\n"
                "This is a valid audit result: LegLens does not "
                "manufacture a finding when evidence is insufficient.",
            )
            return

        sections = []

        for index, finding in enumerate(findings, 1):
            lines = [
                f"FINDING {index}",
                "=" * 72,
                f"Scheme:      {finding.get('scheme_type')}",
                f"Confidence:  {finding.get('confidence')}",
                f"Exposure:    ${finding.get('peso_amount', 0):,.2f} MXN",
                f"Entities:    {', '.join(finding.get('entities', []))}",
                "",
                "Narrative:",
                finding.get("narrative", ""),
                "",
                "Rule:",
                finding.get("rule_broken", ""),
                "",
                "Exhibits:",
            ]

            for exhibit in finding.get("exhibits", []):
                lines.append(
                    "  • "
                    f"{exhibit.get('source_table')}."
                    f"{exhibit.get('record_id')}"
                    + (
                        f" — {exhibit.get('note')}"
                        if exhibit.get("note")
                        else ""
                    )
                )

            money_trail = finding.get("money_trail", [])

            lines.extend(["", "Money trail:"])

            if money_trail:
                for step in money_trail:
                    lines.append("  • " + json.dumps(
                        step,
                        ensure_ascii=False,
                    ))
            else:
                lines.append(
                    "  No deterministic money trail asserted."
                )

            sections.append("\n".join(lines))

        self._replace(
            self.findings_text,
            "\n\n".join(sections),
        )

    def _render_leads(self, leads):
        if not leads:
            self._replace(
                self.declined_text,
                "No declined/inconclusive leads in this submission.",
            )
            return

        sections = []

        for index, lead in enumerate(leads, 1):
            sections.append(
                "\n".join([
                    f"LEAD {index}",
                    "=" * 72,
                    f"Entity:    {lead.get('entity')}",
                    f"Closed by: {lead.get('closed_by')}",
                    "",
                    "Signal:",
                    lead.get("signal", ""),
                    "",
                    "Reason:",
                    lead.get("reason", ""),
                    "",
                    "Tools:",
                    ", ".join(
                        lead.get("tool_calls_made") or []
                    ) or "None recorded",
                ])
            )

        self._replace(
            self.declined_text,
            "\n\n".join(sections),
        )

    @staticmethod
    def _replace(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")


if __name__ == "__main__":
    app = LegLensApp()
    app.mainloop()
