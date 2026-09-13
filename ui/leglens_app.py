"""LedgerLens - Forensic Auditor desktop interface.

Adapted from the original LegLens.py on the feature/frontend branch.  The visual
design, layout and copy are unchanged.  Three things were rewired:

1. Authentication no longer reads UsernamePassword.xlsx.  That file held
   usernames and passwords in clear text inside a public repository.  Logins now
   go through ui.auth, which stores only salted PBKDF2 verifiers.
2. The file picker now runs an audit.  It previously updated a label and
   stopped.  It now accepts an estate (estate.db or estate_csv.zip), runs the
   deterministic pipeline on a worker thread so the window stays responsive, and
   renders findings, declined leads and run cost.
3. Analysis History and Reports are wired to the runs the app has produced.

Run it with:

    python -m ui.leglens_app

No network access is required or made.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from tkinter import messagebox, filedialog

import customtkinter as ctk

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui import auth, case_file, results_view
from ui.audit_bridge import EstateFormatError, run_audit
from ui.theme import (BG_COLOR, BLUE, BLUE_HOVER, BORDER_COLOR, CARD_COLOR,
                      CARD_LIGHT, ERROR, SUCCESS, TEXT_MUTED, TEXT_PRIMARY,
                      TEXT_SECONDARY, WARNING)

RUNS_DIR = REPO_ROOT / "runs"

# Configuration
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Main windows
root = ctk.CTk()
root.title("LedgerLens | Forensic Auditor")
root.geometry("1000x650")
root.minsize(850, 550)
root.configure(fg_color=BG_COLOR)

# Function
def center_window(window, width, height):
    """Centers the window in the screen."""
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()

    x = int((screen_width - width) / 2)
    y = int((screen_height - height) / 2)

    window.geometry(f"{width}x{height}+{x}+{y}")


def clear_error():
    """Clears the error message in the log in."""
    lbl_error.configure(text="")


def toggle_password():
    """Show or hide the password."""

    if entry_password.cget("show") == "*":
        entry_password.configure(show="")
        btn_show_password.configure(text="Hide")
    else:
        entry_password.configure(show="*")
        btn_show_password.configure(text="Show")

# Users registration
def register_user():
    """
    Open the window to create a new account.
    """

    register_window = ctk.CTkToplevel(root)

    register_window.title("LedgerLens | Create Account")
    register_window.geometry("500x600")
    register_window.resizable(False, False)
    register_window.configure(fg_color=BG_COLOR)

    center_window(register_window, 500, 600)

    register_window.transient(root)
    register_window.grab_set()
    register_window.focus()

    # Container
    register_container = ctk.CTkFrame(
        register_window,
        fg_color="transparent"
    )
    register_container.place(
        relx=0.5,
        rely=0.5,
        anchor="center"
    )

    # Branding
    branding = ctk.CTkFrame(
        register_container,
        fg_color="transparent"
    )
    branding.pack(pady=(0, 20))

    lbl_brand = ctk.CTkLabel(
        branding,
        text="LEDGERLENS",
        font=ctk.CTkFont(
            size=22,
            weight="bold"
        ),
        text_color=TEXT_PRIMARY
    )
    lbl_brand.pack()

    brand_line = ctk.CTkFrame(
        branding,
        width=45,
        height=3,
        fg_color=BLUE,
        corner_radius=2
    )
    brand_line.pack(pady=(6, 8))

    lbl_product = ctk.CTkLabel(
        branding,
        text="FORENSIC AUDITOR",
        font=ctk.CTkFont(
            size=10,
            weight="bold"
        ),
        text_color=TEXT_MUTED
    )
    lbl_product.pack()

    # Register card
    register_card = ctk.CTkFrame(
        register_container,
        width=390,
        height=450,
        fg_color=CARD_COLOR,
        corner_radius=16,
        border_width=1,
        border_color=BORDER_COLOR
    )
    register_card.pack()
    register_card.pack_propagate(False)

    # Header
    header = ctk.CTkFrame(
        register_card,
        fg_color="transparent"
    )
    header.pack(
        fill="x",
        padx=40,
        pady=(30, 22)
    )

    lbl_title = ctk.CTkLabel(
        header,
        text="Create your account",
        font=ctk.CTkFont(
            size=22,
            weight="bold"
        ),
        text_color=TEXT_PRIMARY
    )
    lbl_title.pack(anchor="w")

    lbl_subtitle = ctk.CTkLabel(
        header,
        text="Create credentials to access the workspace.",
        font=ctk.CTkFont(size=11),
        text_color=TEXT_SECONDARY
    )
    lbl_subtitle.pack(
        anchor="w",
        pady=(5, 0)
    )

    # Username
    lbl_username = ctk.CTkLabel(
        register_card,
        text="USERNAME",
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(
            size=10,
            weight="bold"
        )
    )
    lbl_username.pack(
        anchor="w",
        padx=40,
        pady=(0, 7)
    )

    entry_register_user = ctk.CTkEntry(
        register_card,
        placeholder_text="Choose a username",
        width=310,
        height=42,
        corner_radius=8,
        border_width=1,
        border_color=BORDER_COLOR,
        fg_color="#15181C",
        text_color=TEXT_PRIMARY,
        placeholder_text_color=TEXT_MUTED
    )
    entry_register_user.pack(
        padx=40,
        pady=(0, 17)
    )

    # Password
    lbl_register_password = ctk.CTkLabel(
        register_card,
        text="PASSWORD",
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(
            size=10,
            weight="bold"
        )
    )
    lbl_register_password.pack(
        anchor="w",
        padx=40,
        pady=(0, 7)
    )

    entry_register_password = ctk.CTkEntry(
        register_card,
        placeholder_text="Create a password",
        show="*",
        width=310,
        height=42,
        corner_radius=8,
        border_width=1,
        border_color=BORDER_COLOR,
        fg_color="#15181C",
        text_color=TEXT_PRIMARY,
        placeholder_text_color=TEXT_MUTED
    )
    entry_register_password.pack(
        padx=40,
        pady=(0, 17)
    )

    # Confirm password
    lbl_confirm_password = ctk.CTkLabel(
        register_card,
        text="CONFIRM PASSWORD",
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(
            size=10,
            weight="bold"
        )
    )
    lbl_confirm_password.pack(
        anchor="w",
        padx=40,
        pady=(0, 7)
    )

    entry_confirm_password = ctk.CTkEntry(
        register_card,
        placeholder_text="Confirm your password",
        show="*",
        width=310,
        height=42,
        corner_radius=8,
        border_width=1,
        border_color=BORDER_COLOR,
        fg_color="#15181C",
        text_color=TEXT_PRIMARY,
        placeholder_text_color=TEXT_MUTED
    )
    entry_confirm_password.pack(
        padx=40,
        pady=(0, 8)
    )

    # Error status
    lbl_register_error = ctk.CTkLabel(
        register_card,
        text="",
        text_color=ERROR,
        font=ctk.CTkFont(
            size=10,
            weight="bold"
        )
    )
    lbl_register_error.pack(
        pady=(0, 7)
    )

    # Create account
    def save_user():

        username = entry_register_user.get().strip()
        password = entry_register_password.get().strip()
        confirm_password = entry_confirm_password.get().strip()

        if not username or not password or not confirm_password:

            lbl_register_error.configure(
                text="Please complete all fields."
            )
            return

        if len(username) < 3:

            lbl_register_error.configure(
                text="Username must contain at least 3 characters."
            )
            return

        if len(password) < 6:

            lbl_register_error.configure(
                text="Password must contain at least 6 characters."
            )
            return

        # Confirm password
        if password != confirm_password:

            lbl_register_error.configure(
                text="Passwords do not match."
            )
            entry_confirm_password.delete(0, "end")
            return

        # Credential store: salted PBKDF2 verifier, never the password itself.
        try:

            auth.create_user(username, password)

        except auth.AuthError as error:

            lbl_register_error.configure(text=str(error))
            return

        except Exception as error:

            messagebox.showerror(
                "System Error",
                "The account could not be created: " + str(error),
                parent=register_window
            )
            return

        messagebox.showinfo(
            "Account Created",
            "Account '" + username + "' was successfully created.",
            parent=register_window
        )

        entry_user.delete(0, "end")
        entry_user.insert(0, username)

        entry_password.delete(0, "end")

        register_window.destroy()

    # Button
    btn_create = ctk.CTkButton(
        register_card,
        text="Create Account",
        width=310,
        height=43,
        corner_radius=8,
        fg_color=BLUE,
        hover_color=BLUE_HOVER,
        font=ctk.CTkFont(
            size=13,
            weight="bold"
        ),
        command=save_user
    )
    btn_create.pack(
        padx=40,
        pady=(2, 10)
    )

    # Return to log in
    btn_back = ctk.CTkButton(
        register_card,
        text="Already have an account? Sign in",
        width=310,
        height=30,
        corner_radius=6,
        fg_color="transparent",
        hover_color=CARD_LIGHT,
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(size=10),
        command=register_window.destroy
    )
    btn_back.pack(
        padx=40
    )

    # Enter to register
    register_window.bind(
        "<Return>",
        lambda event: save_user()
    )

    entry_register_user.focus()

# Workspace
def initial_page():

    initialpage = ctk.CTkToplevel(root)
    initialpage.title("LedgerLens | Forensic Auditor")
    initialpage.minsize(950, 600)
    initialpage.configure(fg_color=BG_COLOR)

    # Open the workspace centred on wherever the login window actually is,
    # rather than on the screen centre. Without this the window appears to jump
    # across the display at the moment of login.
    root.update_idletasks()
    cx = root.winfo_x() + root.winfo_width() // 2
    cy = root.winfo_y() + root.winfo_height() // 2
    initialpage.geometry("1100x700+%d+%d" % (max(cx - 550, 0), max(cy - 350, 0)))

    # Hide the login window only once the workspace has actually been mapped.
    # Withdrawing first leaves a gap with no window on screen, which reads as a
    # flicker or as the app having closed.
    initialpage.update_idletasks()
    initialpage.deiconify()
    root.withdraw()
    initialpage.lift()
    initialpage.focus_force()

    # Deliberately NO grab_set() here. The workspace is the main window, not a
    # modal dialog; an event grab on it makes the file picker and the message
    # boxes fight for focus, which is what made the window appear to change on
    # login. The registration dialog keeps its grab, because that one IS modal.

    # Sidebar
    sidebar = ctk.CTkFrame(
        initialpage,
        width=235,
        corner_radius=0,
        fg_color="#15181C"
    )
    sidebar.pack(
        side="left",
        fill="y"
    )
    sidebar.pack_propagate(False)

    # Branding
    logo_container = ctk.CTkFrame(
        sidebar,
        fg_color="transparent"
    )
    logo_container.pack(
        fill="x",
        padx=25,
        pady=(30, 20)
    )

    logo = ctk.CTkLabel(
        logo_container,
        text="LEDGERLENS",
        font=ctk.CTkFont(
            size=21,
            weight="bold"
        ),
        text_color=TEXT_PRIMARY
    )
    logo.pack(anchor="w")

    logo_line = ctk.CTkFrame(
        logo_container,
        height=3,
        width=42,
        fg_color=BLUE,
        corner_radius=2
    )
    logo_line.pack(
        anchor="w",
        pady=(6, 0)
    )

    subtitle = ctk.CTkLabel(
        logo_container,
        text="FORENSIC AUDITOR",
        font=ctk.CTkFont(
            size=10,
            weight="bold"
        ),
        text_color=TEXT_MUTED
    )
    subtitle.pack(
        anchor="w",
        pady=(10, 0)
    )

    # Separator
    separator = ctk.CTkFrame(
        sidebar,
        height=1,
        fg_color=BORDER_COLOR
    )
    separator.pack(
        fill="x",
        padx=20,
        pady=(5, 25)
    )

    # Navigation title
    nav_title = ctk.CTkLabel(
        sidebar,
        text="WORKSPACE",
        font=ctk.CTkFont(
            size=10,
            weight="bold"
        ),
        text_color=TEXT_MUTED
    )
    nav_title.pack(
        anchor="w",
        padx=25,
        pady=(0, 10)
    )

    # Active button
    btn_dashboard = ctk.CTkButton(
        sidebar,
        text="  Analysis Workspace",
        anchor="w",
        height=42,
        corner_radius=7,
        fg_color=BLUE,
        hover_color=BLUE_HOVER,
        text_color=TEXT_PRIMARY,
        font=ctk.CTkFont(
            size=13,
            weight="bold"
        )
    )
    btn_dashboard.pack(
        fill="x",
        padx=18,
        pady=3
    )

    # History
    btn_history = ctk.CTkButton(
        sidebar,
        text="  Analysis History",
        anchor="w",
        height=42,
        corner_radius=7,
        fg_color="transparent",
        hover_color=CARD_LIGHT,
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(size=13)
    )
    btn_history.pack(
        fill="x",
        padx=18,
        pady=3
    )

    # Reports
    btn_reports = ctk.CTkButton(
        sidebar,
        text="  Reports",
        anchor="w",
        height=42,
        corner_radius=7,
        fg_color="transparent",
        hover_color=CARD_LIGHT,
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(size=13)
    )
    btn_reports.pack(
        fill="x",
        padx=18,
        pady=3
    )

    # Bottom
    sidebar_bottom = ctk.CTkFrame(
        sidebar,
        fg_color="transparent"
    )
    sidebar_bottom.pack(
        side="bottom",
        fill="x",
        padx=18,
        pady=20
    )

    system_status = ctk.CTkLabel(
        sidebar_bottom,
        text="●  SYSTEM ONLINE",
        text_color=SUCCESS,
        font=ctk.CTkFont(
            size=10,
            weight="bold"
        )
    )
    system_status.pack(
        anchor="w",
        padx=7,
        pady=(0, 15)
    )

    def logout():

        initialpage.destroy()
        root.deiconify()
        root.lift()
        root.focus_force()
        entry_password.delete(0, "end")
        clear_error()

    btn_logout = ctk.CTkButton(
        sidebar_bottom,
        text="Log out",
        height=38,
        corner_radius=7,
        fg_color="transparent",
        border_width=1,
        border_color=BORDER_COLOR,
        hover_color=CARD_LIGHT,
        text_color=TEXT_SECONDARY,
        command=logout
    )
    btn_logout.pack(fill="x")

    # Main content
    main = ctk.CTkFrame(
        initialpage,
        fg_color=BG_COLOR,
        corner_radius=0
    )
    main.pack(
        side="right",
        fill="both",
        expand=True
    )

    # Header
    header = ctk.CTkFrame(
        main,
        fg_color="transparent"
    )
    header.pack(
        fill="x",
        padx=45,
        pady=(35, 20)
    )

    header_left = ctk.CTkFrame(
        header,
        fg_color="transparent"
    )
    header_left.pack(side="left")

    page_title = ctk.CTkLabel(
        header_left,
        text="Analysis Workspace",
        font=ctk.CTkFont(
            size=28,
            weight="bold"
        ),
        text_color=TEXT_PRIMARY
    )
    page_title.pack(anchor="w")

    page_description = ctk.CTkLabel(
        header_left,
        text="Upload financial data to begin forensic analysis.",
        font=ctk.CTkFont(size=13),
        text_color=TEXT_SECONDARY
    )
    page_description.pack(
        anchor="w",
        pady=(5, 0)
    )

    # Content card
    content_card = ctk.CTkFrame(
        main,
        fg_color=CARD_COLOR,
        corner_radius=15,
        border_width=1,
        border_color=BORDER_COLOR
    )
    content_card.pack(
        fill="both",
        expand=True,
        padx=45,
        pady=(5, 35)
    )

    # Card header
    card_header = ctk.CTkFrame(
        content_card,
        fg_color="transparent"
    )
    card_header.pack(
        fill="x",
        padx=35,
        pady=(30, 10)
    )

    analysis_title = ctk.CTkLabel(
        card_header,
        text="New Analysis",
        font=ctk.CTkFont(
            size=19,
            weight="bold"
        ),
        text_color=TEXT_PRIMARY
    )
    analysis_title.pack(anchor="w")

    analysis_description = ctk.CTkLabel(
        card_header,
        text="Select the eight-table data estate you want to examine.",
        font=ctk.CTkFont(size=12),
        text_color=TEXT_SECONDARY
    )
    analysis_description.pack(
        anchor="w",
        pady=(5, 0)
    )

    # Upload area
    upload_area = ctk.CTkFrame(
        content_card,
        fg_color="#16191D",
        corner_radius=12,
        border_width=1,
        border_color=BORDER_COLOR
    )
    upload_area.pack(
        fill="both",
        expand=True,
        padx=35,
        pady=20
    )

    upload_inner = ctk.CTkFrame(
        upload_area,
        fg_color="transparent"
    )
    upload_inner.place(
        relx=0.5,
        rely=0.5,
        anchor="center"
    )

    # Icon
    upload_icon = ctk.CTkLabel(
        upload_inner,
        text="↑",
        width=65,
        height=65,
        corner_radius=32,
        fg_color="#1D334A",
        text_color="#6FADE8",
        font=ctk.CTkFont(
            size=32,
            weight="bold"
        )
    )
    upload_icon.pack(pady=(0, 18))

    upload_title = ctk.CTkLabel(
        upload_inner,
        text="Select a data estate to audit",
        font=ctk.CTkFont(
            size=17,
            weight="bold"
        ),
        text_color=TEXT_PRIMARY
    )
    upload_title.pack()

    upload_description = ctk.CTkLabel(
        upload_inner,
        text="Supported: estate.db (SQLite) or estate_csv.zip",
        font=ctk.CTkFont(size=11),
        text_color=TEXT_MUTED
    )
    upload_description.pack(
        pady=(6, 18)
    )

    lbl_status_archivo = ctk.CTkLabel(
        upload_inner,
        text="No estate selected",
        text_color=TEXT_MUTED,
        font=ctk.CTkFont(
            size=12,
            weight="bold"
        )
    )
    lbl_status_archivo.pack(
        pady=(0, 15)
    )

    # Select an estate and run the audit
    state = {"result": None, "busy": False}

    def set_busy(message):
        upload_title.configure(text=message)
        upload_description.configure(
            text="This runs locally. No network call is made."
        )
        btn_upload.configure(state="disabled", text="Working...")
        upload_icon.configure(text="*")

    def show_failure(message):
        state["busy"] = False
        btn_upload.configure(state="normal", text="Select Estate")
        upload_icon.configure(text="!", fg_color="#3A1D1D", text_color=ERROR)
        upload_title.configure(text="That file could not be analysed")
        upload_description.configure(text=message)
        lbl_status_archivo.configure(text="", text_color=TEXT_MUTED)

    def show_results(result):
        state["busy"] = False
        state["result"] = result

        # Replace the upload prompt with the report. The card header stays, so
        # the user keeps their bearings.
        upload_area.destroy()

        analysis_title.configure(text="Analysis Report")
        analysis_description.configure(
            text="Estate: " + os.path.basename(result.estate_path)
                 + "   -   "
                 + ", ".join("%s %d" % (t, n)
                             for t, n in result.table_counts.items())
        )

        results_frame = ctk.CTkFrame(
            content_card,
            fg_color="transparent"
        )
        results_frame.pack(
            fill="both",
            expand=True,
            padx=25,
            pady=(0, 20)
        )
        results_view.render(results_frame, result)

        def start_over():
            results_frame.destroy()
            new_analysis_bar.destroy()
            initialpage.destroy()
            initial_page()

        new_analysis_bar = ctk.CTkFrame(
            content_card,
            fg_color="transparent"
        )
        new_analysis_bar.pack(fill="x", padx=35, pady=(0, 18))

        ctk.CTkButton(
            new_analysis_bar,
            text="New Analysis",
            width=150,
            height=36,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=BORDER_COLOR,
            hover_color=CARD_LIGHT,
            text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=start_over
        ).pack(side="right")

    def submit_archive():

        if state["busy"]:
            return

        archive_route = filedialog.askopenfilename(
            title="Select a Data Estate",
            filetypes=[
                ("Data estate", "*.db *.sqlite *.sqlite3 *.zip"),
                ("SQLite estate", "*.db *.sqlite *.sqlite3"),
                ("Estate CSV bundle", "*.zip"),
                ("All files", "*.*")
            ]
        )

        if not archive_route:
            return

        lbl_status_archivo.configure(
            text="  " + os.path.basename(archive_route),
            text_color=SUCCESS
        )

        state["busy"] = True
        set_busy("Analysing the estate...")

        def worker():
            try:
                result = run_audit(archive_route)
            except EstateFormatError as error:
                initialpage.after(0, show_failure, str(error))
                return
            except Exception as error:  # noqa: BLE001 - surfaced to the user
                initialpage.after(
                    0, show_failure,
                    "The audit stopped with an error: " + str(error)
                )
                return
            initialpage.after(0, show_results, result)

        # A worker thread keeps the window responsive. Every widget update is
        # marshalled back onto the Tk thread with after().
        threading.Thread(target=worker, daemon=True).start()

    btn_upload = ctk.CTkButton(
        upload_inner,
        text="Select Estate",
        width=190,
        height=42,
        corner_radius=8,
        fg_color=BLUE,
        hover_color=BLUE_HOVER,
        font=ctk.CTkFont(
            size=13,
            weight="bold"
        ),
        command=submit_archive
    )
    btn_upload.pack()

    # Sidebar navigation
    def show_history():
        runs = sorted(RUNS_DIR.glob("*/submission.json")) if RUNS_DIR.exists() else []
        if not runs:
            messagebox.showinfo(
                "Analysis History",
                "No completed analysis yet. Run one from the Analysis "
                "Workspace and it will be recorded here.",
                parent=initialpage
            )
            return
        lines = []
        for path in runs[-15:]:
            try:
                import json
                payload = json.loads(path.read_text(encoding="utf-8"))
                lines.append(
                    "%s\n    seed %s  -  %d finding(s), %d lead(s) closed"
                    % (path.parent.name, payload.get("seed"),
                       len(payload.get("findings", [])),
                       len(payload.get("leads_not_pursued", [])))
                )
            except Exception:
                lines.append("%s  (unreadable)" % path.parent.name)
        messagebox.showinfo(
            "Analysis History",
            "%d completed analysis run(s):\n\n%s"
            % (len(runs), "\n".join(lines)),
            parent=initialpage
        )

    def show_reports():
        result = state.get("result")
        if result is None or not result.submission_path:
            messagebox.showinfo(
                "Reports",
                "Run an analysis first. The submission for each run is written "
                "to the runs folder and listed under Analysis History.",
                parent=initialpage
            )
            return
        results_view._reveal(result.submission_path)

    btn_history.configure(command=show_history)
    btn_reports.configure(command=show_reports)

    # Footer
    footer = ctk.CTkLabel(
        main,
        text="Forensic Auditor  •  Secure Analysis Environment",
        font=ctk.CTkFont(size=10),
        text_color=TEXT_MUTED
    )
    footer.place(
        relx=0.5,
        rely=0.975,
        anchor="center"
    )

    # Close
    def close_new_window():

        initialpage.destroy()
        root.deiconify()
        root.lift()
        root.focus_force()
        entry_password.delete(0, "end")
        clear_error()

    initialpage.protocol(
        "WM_DELETE_WINDOW",
        close_new_window
    )

# Log in validation
def validar_login():

    user_login = entry_user.get().strip()
    password_login = entry_password.get()

    clear_error()

    if not user_login or not password_login:

        lbl_error.configure(
            text="Please enter your username and password."
        )
        return

    try:

        if auth.verify_user(user_login, password_login):

            initial_page()

        else:

            # Deliberately does not say which field was wrong: that would tell
            # an attacker which usernames exist.
            lbl_error.configure(
                text="Incorrect username or password."
            )

            entry_password.delete(0, "end")

    except auth.AuthError as error:

        messagebox.showerror("System Error", str(error))

    except Exception as error:

        messagebox.showerror(
            "System Error",
            "An unexpected error occurred: " + str(error)
        )


# Log in User interface
login_container = ctk.CTkFrame(
    root,
    fg_color="transparent"
)
login_container.place(
    relx=0.5,
    rely=0.5,
    anchor="center"
)

# Branding
branding = ctk.CTkFrame(
    login_container,
    fg_color="transparent"
)
branding.pack(pady=(0, 25))

lbl_brand = ctk.CTkLabel(
    branding,
    text="LEDGERLENS",
    font=ctk.CTkFont(
        size=25,
        weight="bold"
    ),
    text_color=TEXT_PRIMARY
)
lbl_brand.pack()

brand_line = ctk.CTkFrame(
    branding,
    width=50,
    height=3,
    fg_color=BLUE,
    corner_radius=2
)
brand_line.pack(pady=(7, 10))

lbl_product = ctk.CTkLabel(
    branding,
    text="FORENSIC AUDITOR",
    font=ctk.CTkFont(
        size=11,
        weight="bold"
    ),
    text_color=TEXT_MUTED
)
lbl_product.pack()

# Log in card
login_card = ctk.CTkFrame(
    login_container,
    width=390,
    height=430,
    fg_color=CARD_COLOR,
    corner_radius=16,
    border_width=1,
    border_color=BORDER_COLOR
)
login_card.pack()
login_card.pack_propagate(False)

# Header
login_header = ctk.CTkFrame(
    login_card,
    fg_color="transparent"
)
login_header.pack(
    fill="x",
    padx=40,
    pady=(35, 25)
)

lbl_welcome = ctk.CTkLabel(
    login_header,
    text="Welcome back",
    font=ctk.CTkFont(
        size=24,
        weight="bold"
    ),
    text_color=TEXT_PRIMARY
)
lbl_welcome.pack(anchor="w")

lbl_instruction = ctk.CTkLabel(
    login_header,
    text="Sign in to access the analysis environment.",
    font=ctk.CTkFont(size=12),
    text_color=TEXT_SECONDARY
)
lbl_instruction.pack(
    anchor="w",
    pady=(6, 0)
)

# Username
lbl_user = ctk.CTkLabel(
    login_card,
    text="USERNAME",
    text_color=TEXT_SECONDARY,
    font=ctk.CTkFont(
        size=10,
        weight="bold"
    )
)
lbl_user.pack(
    anchor="w",
    padx=40,
    pady=(0, 7)
)

entry_user = ctk.CTkEntry(
    login_card,
    placeholder_text="Enter your username",
    width=310,
    height=42,
    corner_radius=8,
    border_width=1,
    border_color=BORDER_COLOR,
    fg_color="#15181C",
    text_color=TEXT_PRIMARY,
    placeholder_text_color=TEXT_MUTED
)
entry_user.pack(
    padx=40,
    pady=(0, 17)
)

# Password
lbl_password = ctk.CTkLabel(
    login_card,
    text="PASSWORD",
    text_color=TEXT_SECONDARY,
    font=ctk.CTkFont(
        size=10,
        weight="bold"
    )
)
lbl_password.pack(
    anchor="w",
    padx=40,
    pady=(0, 7)
)

password_container = ctk.CTkFrame(
    login_card,
    width=310,
    height=42,
    fg_color="transparent"
)
password_container.pack(
    padx=40,
    pady=(0, 5)
)

password_container.pack_propagate(False)

entry_password = ctk.CTkEntry(
    password_container,
    placeholder_text="Enter your password",
    show="*",
    height=42,
    corner_radius=8,
    border_width=1,
    border_color=BORDER_COLOR,
    fg_color="#15181C",
    text_color=TEXT_PRIMARY,
    placeholder_text_color=TEXT_MUTED
)
entry_password.pack(
    side="left",
    fill="both",
    expand=True
)

btn_show_password = ctk.CTkButton(
    password_container,
    text="Show",
    width=55,
    height=32,
    corner_radius=6,
    fg_color="transparent",
    hover_color=CARD_LIGHT,
    text_color=TEXT_MUTED,
    font=ctk.CTkFont(size=10),
    command=toggle_password
)
btn_show_password.place(
    relx=1,
    rely=0.5,
    anchor="e",
    x=-5
)

# Error
lbl_error = ctk.CTkLabel(
    login_card,
    text="",
    text_color=ERROR,
    font=ctk.CTkFont(
        size=11,
        weight="bold"
    )
)
lbl_error.pack(
    pady=(3, 5)
)

# Log in button
button = ctk.CTkButton(
    login_card,
    text="Sign In",
    width=310,
    height=43,
    corner_radius=8,
    fg_color=BLUE,
    hover_color=BLUE_HOVER,
    text_color=TEXT_PRIMARY,
    font=ctk.CTkFont(
        size=13,
        weight="bold"
    ),
    command=validar_login
)
button.pack(
    padx=40,
    pady=(5, 10)
)

# Create account
account_frame = ctk.CTkFrame(
    login_card,
    fg_color="transparent"
)
account_frame.pack(
    pady=(0, 5)
)

lbl_no_account = ctk.CTkLabel(
    account_frame,
    text="Don't have an account?",
    font=ctk.CTkFont(size=10),
    text_color=TEXT_MUTED
)
lbl_no_account.pack(
    side="left",
    padx=(0, 4)
)

btn_create_account = ctk.CTkButton(
    account_frame,
    text="Create one",
    width=75,
    height=25,
    corner_radius=5,
    fg_color="transparent",
    hover_color=CARD_LIGHT,
    text_color="#6FADE8",
    font=ctk.CTkFont(
        size=10,
        weight="bold"
    ),
    command=register_user
)
btn_create_account.pack(
    side="left"
)

# Security text
security_label = ctk.CTkLabel(
    login_card,
    text="Secure access • Authorized personnel only",
    font=ctk.CTkFont(size=9),
    text_color=TEXT_MUTED
)
security_label.pack(
    side="bottom",
    pady=18
)


# Shortcuts
root.bind(
    "<Return>",
    lambda event: validar_login()
)

entry_user.bind(
    "<KeyRelease>",
    lambda event: clear_error()
)

entry_password.bind(
    "<KeyRelease>",
    lambda event: clear_error()
)


center_window(root, 1000, 650)


def main() -> int:
    """Start the interface.

    Guarded so that importing this module builds the widgets without seizing
    the thread. Without the guard, any import -- a test, a tool, another
    module -- blocks forever inside mainloop().
    """
    if auth.user_count() == 0:
        lbl_error.configure(
            text="No accounts yet. Use 'Create account' to register one."
        )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
