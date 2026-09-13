import customtkinter as ctk
import pandas as pd
from tkinter import messagebox, filedialog
import os

# Configuration
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Colors
BG_COLOR = "#111315"
CARD_COLOR = "#1A1D21"
CARD_LIGHT = "#20242A"
BORDER_COLOR = "#2D333A"

BLUE = "#2474C8"
BLUE_HOVER = "#1D5FA8"

TEXT_PRIMARY = "#F2F4F7"
TEXT_SECONDARY = "#A7ADB5"
TEXT_MUTED = "#707780"

SUCCESS = "#2CC985"
ERROR = "#E05252"
WARNING = "#E0A84B"

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

        # Excel
        try:

            file_name = "UsernamePassword.xlsx"

            if os.path.exists(file_name):

                user_data = pd.read_excel(file_name)

                if "Username" not in user_data.columns or \
                   "Password" not in user_data.columns:

                    messagebox.showerror(
                        "Database Error",
                        "The Excel file must contain "
                        "'Username' and 'Password' columns.",
                        parent=register_window
                    )
                    return

            else:

                user_data = pd.DataFrame(
                    columns=["Username", "Password"]
                )

            user_data["Username"] = (
                user_data["Username"]
                .astype(str)
                .str.strip()
            )

            username_exists = (
                user_data["Username"]
                .str.lower()
                .eq(username.lower())
                .any()
            )

            if username_exists:

                lbl_register_error.configure(
                    text="That username is already registered."
                )
                return

            new_user = pd.DataFrame({
                "Username": [username],
                "Password": [password]
            })

            user_data = pd.concat(
                [user_data, new_user],
                ignore_index=True
            )

            user_data.to_excel(
                file_name,
                index=False
            )

            messagebox.showinfo(
                "Account Created",
                f"Account '{username}' was successfully created.",
                parent=register_window
            )

            entry_user.delete(0, "end")
            entry_user.insert(0, username)

            entry_password.delete(0, "end")

            register_window.destroy()

        except PermissionError:

            messagebox.showerror(
                "File Error",
                "The Excel file is currently open.\n\n"
                "Please close 'UsernamePassword.xlsx' "
                "and try again.",
                parent=register_window
            )

        except Exception as error:

            messagebox.showerror(
                "System Error",
                f"An unexpected error occurred:\n{error}",
                parent=register_window
            )

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

    root.withdraw()

    initialpage = ctk.CTkToplevel(root)
    initialpage.title("LedgerLens | Forensic Auditor")
    initialpage.geometry("1100x700")
    initialpage.minsize(950, 600)
    initialpage.configure(fg_color=BG_COLOR)

    center_window(initialpage, 1100, 700)

    initialpage.focus()
    initialpage.grab_set()

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
        text="Select a dataset containing the information you want to examine.",
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
        text="Upload a file for analysis",
        font=ctk.CTkFont(
            size=17,
            weight="bold"
        ),
        text_color=TEXT_PRIMARY
    )
    upload_title.pack()

    upload_description = ctk.CTkLabel(
        upload_inner,
        text="Supported formats: Excel (.xlsx, .xls) and CSV (.csv)",
        font=ctk.CTkFont(size=11),
        text_color=TEXT_MUTED
    )
    upload_description.pack(
        pady=(6, 18)
    )

    lbl_status_archivo = ctk.CTkLabel(
        upload_inner,
        text="No file selected",
        text_color=TEXT_MUTED,
        font=ctk.CTkFont(
            size=12,
            weight="bold"
        )
    )
    lbl_status_archivo.pack(
        pady=(0, 15)
    )

    # Select archive
    def submit_archive():

        archive_route = filedialog.askopenfilename(
            title="Select File for Analysis",
            filetypes=[
                ("Excel files", "*.xlsx *.xls"),
                ("CSV files", "*.csv"),
                ("PDF", "*.pdf"),
                ("All files", "*.*")
            ]
        )

        if archive_route:

            nombre_archivo = os.path.basename(
                archive_route
            )

            lbl_status_archivo.configure(
                text=f"✓  {nombre_archivo}",
                text_color=SUCCESS
            )

            upload_title.configure(
                text="File ready for analysis"
            )

            upload_description.configure(
                text="The selected dataset can now be processed."
            )

            btn_upload.configure(
                text="Change File"
            )

    btn_upload = ctk.CTkButton(
        upload_inner,
        text="Select File",
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

    initialpage.protocol(
        "WM_DELETE_WINDOW",
        close_new_window
    )

# Log in validation
def validar_login():

    user_login = entry_user.get().strip()
    password_login = entry_password.get().strip()

    clear_error()

    if not user_login or not password_login:

        lbl_error.configure(
            text="Please enter your username and password."
        )
        return

    try:

        user_data = pd.read_excel(
            "UsernamePassword.xlsx"
        )

        user_data["Username"] = (
            user_data["Username"]
            .astype(str)
            .str.strip()
        )

        user_data["Password"] = (
            user_data["Password"]
            .astype(str)
            .str.strip()
        )

        coincidence = user_data[
            (user_data["Username"] == user_login) &
            (user_data["Password"] == password_login)
        ]

        if not coincidence.empty:

            initial_page()

        else:

            lbl_error.configure(
                text="Incorrect username or password."
            )

            entry_password.delete(0, "end")

    except FileNotFoundError:

        messagebox.showerror(
            "System Error",
            "The authentication database "
            "'UsernamePassword.xlsx' was not found."
        )

    except Exception as error:

        messagebox.showerror(
            "System Error",
            f"An unexpected error occurred:\n{error}"
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
root.mainloop()
