"""Renders an :class:`~ui.audit_bridge.AuditResult` inside a CustomTkinter frame.

Kept out of the main app file so the layout can change without touching the
login and navigation code, and so the app file stays close to the original.

The presentation rules here are the scored ones, not decoration:

* A finding shows its **rule broken**, its **amount**, its **confidence** and
  its **exhibits with record ids**.  A judge must be able to walk from the
  accusation to the rows it rests on.
* **Declined leads sit in the body**, not behind a tab, with the reason and the
  lookups that produced it.  "Why didn't you flag vendor X" is answered on the
  page.
* Run cost is shown whether or not it is impressive.  "We don't know" scores
  badly; zero is a real answer.
"""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path

import customtkinter as ctk

from ui import case_file
from ui import theme as T
from ui.audit_bridge import AuditResult


def _money(value: float) -> str:
    return "MXN {:,.2f}".format(value)


def _stat(parent, label: str, value: str, accent: str) -> None:
    box = ctk.CTkFrame(parent, fg_color=T.CARD_LIGHT, corner_radius=10,
                       border_width=1, border_color=T.BORDER_COLOR)
    box.pack(side="left", fill="both", expand=True, padx=5)
    ctk.CTkLabel(box, text=value, font=ctk.CTkFont(size=20, weight="bold"),
                 text_color=accent).pack(anchor="w", padx=16, pady=(14, 0))
    ctk.CTkLabel(box, text=label, font=ctk.CTkFont(size=10),
                 text_color=T.TEXT_MUTED).pack(anchor="w", padx=16, pady=(2, 14))


def _section(parent, title: str, subtitle: str) -> None:
    ctk.CTkLabel(parent, text=title, font=ctk.CTkFont(size=16, weight="bold"),
                 text_color=T.TEXT_PRIMARY).pack(anchor="w", pady=(22, 0))
    ctk.CTkLabel(parent, text=subtitle, font=ctk.CTkFont(size=11),
                 text_color=T.TEXT_MUTED, justify="left",
                 wraplength=760).pack(anchor="w", pady=(2, 8))


def _reveal(path: str) -> None:
    """Open the folder holding the submission, on whichever platform this is."""
    folder = str(Path(path).parent)
    try:
        if sys.platform.startswith("win"):
            os.startfile(folder)  # noqa: S606 - opening a local folder we wrote
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception:
        pass


def render(parent, result: AuditResult) -> ctk.CTkScrollableFrame:
    """Build the results panel inside ``parent`` and return it."""
    view = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    view.pack(fill="both", expand=True, padx=6, pady=6)

    # ---- headline numbers -------------------------------------------------
    stats = ctk.CTkFrame(view, fg_color="transparent")
    stats.pack(fill="x", pady=(4, 2))
    _stat(stats, "FINDINGS", str(len(result.findings)),
          T.ERROR if result.findings else T.SUCCESS)
    _stat(stats, "TOTAL EXPOSURE", _money(result.total_exposure), T.TEXT_PRIMARY)
    _stat(stats, "LEADS CLOSED", str(len(result.leads)), T.TEXT_PRIMARY)
    _stat(stats, "SIGNALS RAISED", str(result.observations), T.TEXT_PRIMARY)

    runbox = ctk.CTkFrame(view, fg_color="transparent")
    runbox.pack(fill="x", pady=(8, 0))
    _stat(runbox, "MODEL CALLS", str(result.llm_calls), T.TEXT_SECONDARY)
    _stat(runbox, "COST", _money(result.mxn_cost), T.TEXT_SECONDARY)
    _stat(runbox, "WALL CLOCK", "%.2f s" % result.wall_clock_seconds,
          T.TEXT_SECONDARY)
    _stat(runbox, "RUN SEED", str(result.seed), T.TEXT_SECONDARY)

    for note in result.warnings:
        ctk.CTkLabel(view, text="!  " + note, font=ctk.CTkFont(size=11),
                     text_color=T.WARNING, justify="left",
                     wraplength=780).pack(anchor="w", pady=(10, 0))

    # ---- findings ---------------------------------------------------------
    if result.findings:
        _section(view, "Findings",
                 "Each accusation was recomputed from the estate and authorised "
                 "by the Evidence Gate before it was printed.")
    else:
        _section(view, "Findings",
                 "No accusation reached the evidentiary standard. An empty "
                 "findings list is a legitimate result.")

    for index, finding in enumerate(result.findings, start=1):
        card = ctk.CTkFrame(view, fg_color=T.CARD_COLOR, corner_radius=12,
                            border_width=1, border_color=T.BORDER_COLOR)
        card.pack(fill="x", pady=6)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 4))
        ctk.CTkLabel(head, text="%d.  %s" % (index, finding.scheme_type),
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=T.TEXT_PRIMARY).pack(side="left")
        ctk.CTkLabel(head, text=finding.confidence.upper(),
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=T.WARNING).pack(side="right")
        ctk.CTkLabel(head, text=_money(finding.peso_amount) + "   ",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=T.TEXT_PRIMARY).pack(side="right")

        for label, value in (("Entities", ", ".join(finding.entities)),
                             ("Rule broken", finding.rule_broken)):
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=1)
            ctk.CTkLabel(row, text=label, width=92, anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=T.TEXT_MUTED).pack(side="left")
            ctk.CTkLabel(row, text=value, anchor="w", justify="left",
                         font=ctk.CTkFont(size=11), text_color=T.TEXT_SECONDARY,
                         wraplength=620).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(card, text=finding.narrative, justify="left",
                     font=ctk.CTkFont(size=11), text_color=T.TEXT_SECONDARY,
                     wraplength=730).pack(anchor="w", padx=18, pady=(8, 4))

        ctk.CTkLabel(card, text="Exhibits (%d)" % len(finding.exhibits),
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=T.TEXT_MUTED).pack(anchor="w", padx=18,
                                                   pady=(6, 2))
        table = ctk.CTkFrame(card, fg_color=T.PANEL_COLOR, corner_radius=8)
        table.pack(fill="x", padx=18, pady=(0, 16))
        for exhibit in finding.exhibits:
            line = ctk.CTkFrame(table, fg_color="transparent")
            line.pack(fill="x", padx=10, pady=3)
            ctk.CTkLabel(line, text=exhibit.exhibit_id, width=62, anchor="w",
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=T.BLUE).pack(side="left")
            ctk.CTkLabel(line, text=exhibit.source_table, width=110, anchor="w",
                         font=ctk.CTkFont(size=10),
                         text_color=T.TEXT_MUTED).pack(side="left")
            ctk.CTkLabel(line, text=exhibit.record_id, anchor="w",
                         font=ctk.CTkFont(size=10),
                         text_color=T.TEXT_SECONDARY,
                         wraplength=420).pack(side="left", fill="x", expand=True)

    # ---- declined leads, in the body ------------------------------------
    _section(view, "Leads investigated and closed (%d)" % len(result.leads),
             "Every signal that did not become an accusation, with the records "
             "that were read to close it.")

    for lead in result.leads:
        card = ctk.CTkFrame(view, fg_color=T.CARD_COLOR, corner_radius=10,
                            border_width=1, border_color=T.BORDER_COLOR)
        card.pack(fill="x", pady=4)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(10, 2))
        ctk.CTkLabel(head, text=lead.entity,
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=T.TEXT_PRIMARY).pack(side="left")
        ctk.CTkLabel(head, text="closed by " + lead.closed_by,
                     font=ctk.CTkFont(size=10),
                     text_color=T.TEXT_MUTED).pack(side="right")
        ctk.CTkLabel(card, text=lead.signal, justify="left",
                     font=ctk.CTkFont(size=10), text_color=T.TEXT_MUTED,
                     wraplength=740).pack(anchor="w", padx=16)
        ctk.CTkLabel(card, text=lead.reason, justify="left",
                     font=ctk.CTkFont(size=11), text_color=T.TEXT_SECONDARY,
                     wraplength=740).pack(anchor="w", padx=16, pady=(4, 2))
        if lead.tool_calls_made:
            ctk.CTkLabel(card, text="lookups: " + ", ".join(lead.tool_calls_made),
                         justify="left", font=ctk.CTkFont(size=9),
                         text_color=T.TEXT_MUTED,
                         wraplength=740).pack(anchor="w", padx=16, pady=(0, 10))
        else:
            ctk.CTkLabel(card, text="").pack(pady=(0, 6))

    # ---- output -----------------------------------------------------------
    if result.submission_path:
        _section(view, "Export",
                 "The case file is the document a judge reads. The submission "
                 "is the machine-readable form the validator checks.")
        out = ctk.CTkFrame(view, fg_color=T.CARD_COLOR, corner_radius=10,
                           border_width=1, border_color=T.BORDER_COLOR)
        out.pack(fill="x", pady=(0, 8))

        status = ctk.CTkLabel(
            out, text=result.submission_path, anchor="w",
            font=ctk.CTkFont(size=10), text_color=T.TEXT_SECONDARY,
            wraplength=440)
        status.pack(side="left", padx=16, pady=14)

        def export_case_file():
            """Write the self-contained HTML case file and open it."""
            try:
                target = Path(result.submission_path).parent / "case_file.html"
                case_file.export(result, target)
            except Exception as error:  # noqa: BLE001 - surfaced to the user
                status.configure(text="Case file could not be written: %s"
                                      % error, text_color=T.ERROR)
                return
            status.configure(text="Case file written: %s" % target,
                             text_color=T.SUCCESS)
            try:
                webbrowser.open(target.resolve().as_uri())
            except Exception:
                _reveal(str(target))

        ctk.CTkButton(out, text="Open folder", width=110, height=32,
                      corner_radius=7, fg_color="transparent", border_width=1,
                      border_color=T.BORDER_COLOR, hover_color=T.CARD_LIGHT,
                      text_color=T.TEXT_SECONDARY,
                      font=ctk.CTkFont(size=11),
                      command=lambda: _reveal(result.submission_path)
                      ).pack(side="right", padx=(6, 16), pady=14)
        ctk.CTkButton(out, text="Export case file", width=150, height=32,
                      corner_radius=7, fg_color=T.BLUE, hover_color=T.BLUE_HOVER,
                      font=ctk.CTkFont(size=11, weight="bold"),
                      command=export_case_file).pack(side="right", pady=14)

        ctk.CTkLabel(view,
                     text="The case file is a single self-contained HTML file: "
                          "no external stylesheet, script or font. It renders "
                          "with the network disabled and prints to PDF.",
                     font=ctk.CTkFont(size=10), text_color=T.TEXT_MUTED,
                     justify="left", wraplength=760).pack(anchor="w",
                                                          pady=(0, 18))

    return view
