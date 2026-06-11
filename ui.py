import asyncio
import httpx
import flet as ft
from core.events import get_gatekeeper_events, get_guardian_events
from core.config import settings, is_configured, reload_settings
from repoguard_agent.agent import invoke_root_agent

# --- Themes & Colors ---
BG_COLOR = "#0f172a"
CARD_COLOR = "#1e293b"
BORDER_COLOR = "#334155"
TEXT_COLOR = "#e2e8f0"
SUB_TEXT_COLOR = "#94a3b8"
ACCENT_COLOR = "#3b82f6"

# --- Components ---

def verdict_badge(v: str):
    cfg = {
        "APPROVED": ("✅ APPROVED", "#166534", "#86efac"),
        "REJECTED": ("🔴 REJECTED", "#7f1d1d", "#fca5a5"),
        "NEEDS_REVIEW": ("🟡 NEEDS REVIEW", "#713f12", "#fde68a"),
    }
    label, bg, fg = cfg.get(v.upper(), (v, "#1e293b", "#94a3b8"))
    return ft.Container(
        content=ft.Text(label, size=10, weight="bold", color=fg),
        bgcolor=bg,
        padding=ft.padding.symmetric(horizontal=10, vertical=2),
        border_radius=999,
    )

def status_badge(s: str):
    is_approved = "approved" in s.lower()
    bg, fg = ("#0c4a6e", "#7dd3fc") if is_approved else ("#4a1d96", "#c4b5fd")
    return ft.Container(
        content=ft.Text(s.upper(), size=10, weight="bold", color=fg),
        bgcolor=bg,
        padding=ft.padding.symmetric(horizontal=10, vertical=2),
        border_radius=999,
    )

# --- Pages ---

async def DashboardPage(page: ft.Page):
    gk_list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)
    gd_list = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)

    config_warning = ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=ft.Colors.ORANGE_400),
            ft.Text("Project not fully configured. Please set GITLAB_PAT and GCP_PROJECT_ID in your .env file to enable Agent features.", color=ft.Colors.ORANGE_400, size=12),
        ], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor="#332b1e",
        padding=10,
        border_radius=10,
        visible=not is_configured()
    )

    async def refresh_events(e=None):
        if not settings.GCP_PROJECT_ID:
            gk_list.controls.clear()
            gk_list.controls.append(ft.Text("GCP_PROJECT_ID missing. Events cannot be loaded.", color=SUB_TEXT_COLOR, italic=True))
            page.update()
            return

        gk_events, gd_events = await asyncio.gather(
            get_gatekeeper_events(ascending=False),
            get_guardian_events(ascending=False),
        )
        
        gk_list.controls.clear()
        if not gk_events:
            gk_list.controls.append(ft.Text("No events yet", color=SUB_TEXT_COLOR, italic=True))
        for e_data in gk_events:
            gk_list.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(e_data['ts'], size=11, color=SUB_TEXT_COLOR, width=140),
                        ft.Text(f"!{e_data['mr_iid']}", size=11, weight="bold", color=ACCENT_COLOR, width=40),
                        ft.Text(e_data.get('mr_title', ''), size=11, expand=True, overflow=ft.TextOverflow.ELLIPSIS),
                        verdict_badge(e_data['verdict']),
                    ]),
                    padding=10,
                    border=ft.Border.only(bottom=ft.BorderSide(1, BORDER_COLOR))
                )
            )

        gd_list.controls.clear()
        if not gd_events:
            gd_list.controls.append(ft.Text("No incidents logged", color=SUB_TEXT_COLOR, italic=True))
        for e_data in gd_events:
            gd_list.controls.append(
                ft.Container(
                    content=ft.Row([
                        ft.Text(e_data['ts'], size=11, color=SUB_TEXT_COLOR, width=140),
                        ft.Text(e_data.get('commit_sha', '')[:8], size=11, color="#f472b6", width=80),
                        ft.Text(e_data.get('error_type', ''), size=11, expand=True, overflow=ft.TextOverflow.ELLIPSIS),
                        status_badge(e_data.get('status', 'pending')),
                    ]),
                    padding=10,
                    border=ft.Border.only(bottom=ft.BorderSide(1, BORDER_COLOR))
                )
            )
        page.update()

    # Initial Load
    await refresh_events()

    return ft.Column([
        config_warning,
        ft.Row([
            ft.Text("🛡️ Dashboard", size=24, weight="bold"),
            ft.IconButton(ft.Icons.REFRESH, on_click=lambda e: asyncio.create_task(refresh_events(e))),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.ResponsiveRow([
            ft.Column([
                ft.Container(
                    content=ft.Column([
                        ft.Text("🛡️ Gatekeeper — MR Verdicts", size=16, weight="bold"),
                        ft.Divider(color=BORDER_COLOR),
                        gk_list,
                    ]),
                    bgcolor=CARD_COLOR,
                    border=ft.Border.all(1, BORDER_COLOR),
                    border_radius=10,
                    padding=20,
                )
            ], col={"sm": 12, "md": 6}),
            ft.Column([
                ft.Container(
                    content=ft.Column([
                        ft.Text("🚨 Guardian — Incident Response", size=16, weight="bold"),
                        ft.Divider(color=BORDER_COLOR),
                        gd_list,
                    ]),
                    bgcolor=CARD_COLOR,
                    border=ft.Border.all(1, BORDER_COLOR),
                    border_radius=10,
                    padding=20,
                )
            ], col={"sm": 12, "md": 6}),
        ]),
    ], spacing=20, expand=True, scroll=ft.ScrollMode.ADAPTIVE)

async def ArchitectPage(page: ft.Page):
    chat_list = ft.ListView(expand=True, spacing=10, padding=10)
    user_input = ft.TextField(
        hint_text="Describe the project you want to create...",
        expand=True,
        border_color=BORDER_COLOR,
        on_submit=lambda _: asyncio.create_task(send_message())
    )

    async def send_message(e=None):
        if not user_input.value: return
        msg = user_input.value
        user_input.value = ""
        chat_list.controls.append(ft.Row([
            ft.Container(
                content=ft.Text(msg, color="white"),
                bgcolor=ACCENT_COLOR,
                padding=10,
                border_radius=ft.BorderRadius.only(top_left=10, top_right=10, bottom_left=10)
            )
        ], alignment=ft.MainAxisAlignment.END))
        page.update()
        
        # Call Architect Agent via Orchestrator
        project_id = settings.GITLAB_PROJECT_ID or 0
        agent_prompt = f"Architect/Scaffolding Request: project_id={project_id}, user_prompt='{msg}'"
        
        # Show a loading indicator
        loading_msg = ft.Row([
            ft.Container(
                content=ft.Row([
                    ft.ProgressRing(width=16, height=16, stroke_width=2),
                    ft.Text(" Architect is thinking...", color=SUB_TEXT_COLOR, size=12, italic=True)
                ]),
                bgcolor=CARD_COLOR,
                padding=10,
                border=ft.Border.all(1, BORDER_COLOR),
                border_radius=ft.BorderRadius.only(top_left=10, top_right=10, bottom_right=10)
            )
        ])
        chat_list.controls.append(loading_msg)
        page.update()

        try:
            response = await invoke_root_agent(agent_prompt)
            chat_list.controls.remove(loading_msg)
            chat_list.controls.append(ft.Row([
                ft.Container(
                    content=ft.Markdown(response, selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB),
                    bgcolor=CARD_COLOR,
                    padding=10,
                    border=ft.Border.all(1, BORDER_COLOR),
                    border_radius=ft.BorderRadius.only(top_left=10, top_right=10, bottom_right=10)
                )
            ]))
        except Exception as exc:
            if loading_msg in chat_list.controls:
                chat_list.controls.remove(loading_msg)
            chat_list.controls.append(ft.Text(f"Error: {exc}", color="red"))
        
        page.update()

    return ft.Column([
        ft.Text("✨ Project Architect", size=24, weight="bold"),
        ft.Text("Vibe-code your new repository. I'll handle the scaffolding, settings, and license.", color=SUB_TEXT_COLOR),
        ft.Container(
            content=chat_list,
            bgcolor=BG_COLOR,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=10,
            expand=True,
        ),
        ft.Row([
            user_input,
            ft.IconButton(ft.Icons.SEND, icon_color=ACCENT_COLOR, on_click=lambda e: asyncio.create_task(send_message(e))),
        ])
    ], spacing=20, expand=True)

async def SettingsPage(page: ft.Page):
    reload_settings() # Ensure latest settings are loaded
    gitlab_pat = ft.TextField(label="GitLab PAT", value=settings.GITLAB_PAT, password=True, can_reveal_password=True, border_color=BORDER_COLOR)
    gcp_project = ft.TextField(label="GCP Project ID", value=settings.GCP_PROJECT_ID, border_color=BORDER_COLOR)
    gcp_location = ft.TextField(label="GCP Location", value=settings.GCP_LOCATION, border_color=BORDER_COLOR)
    use_vertex = ft.Checkbox(label="Use Vertex AI (GCP Mode)", value=settings.GOOGLE_GENAI_USE_VERTEXAI)
    gemini_model = ft.Dropdown(
        label="Gemini Model",
        value=settings.GEMINI_MODEL or "gemini-2.5-flash",
        options=[
            ft.dropdown.Option("gemini-3.5-flash"),
            ft.dropdown.Option("gemini-3.1-flash-lite"),
            ft.dropdown.Option("gemini-3.1-flash-image"),
            ft.dropdown.Option("gemini-3.1-pro-preview"),
            ft.dropdown.Option("gemini-2.5-flash"),
            ft.dropdown.Option("gemini-2.5-pro"),
        ],
        border_color=BORDER_COLOR
    )

    gitlab_status = ft.Text(
        "GitLab OAuth: " + ("✅ Connected" if settings.GITLAB_ACCESS_TOKEN else "❌ Disconnected"),
        color="green" if settings.GITLAB_ACCESS_TOKEN else "red",
        size=12
    )

    async def start_gitlab_oauth(e):
        # We call our own API to get the login URL
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"http://localhost:8000/auth/login")
                if resp.status_code == 200:
                    url = resp.json()["login_url"]
                    await page.launch_url(url)
                else:
                    page.snack_bar = ft.SnackBar(ft.Text(f"Failed to start OAuth: {resp.text}"))
                    page.snack_bar.open = True
            except Exception as exc:
                page.snack_bar = ft.SnackBar(ft.Text(f"Error connecting to auth server: {exc}"))
                page.snack_bar.open = True
            page.update()

    async def save_settings(e):
        settings.GITLAB_PAT = gitlab_pat.value
        settings.GCP_PROJECT_ID = gcp_project.value
        settings.GCP_LOCATION = gcp_location.value
        settings.GEMINI_MODEL = gemini_model.value
        settings.GOOGLE_GENAI_USE_VERTEXAI = use_vertex.value
        
        # Save to .env file
        env_content = (
            f"GITLAB_PAT={settings.GITLAB_PAT}\n"
            f"GITLAB_WEBHOOK_SECRET={settings.GITLAB_WEBHOOK_SECRET}\n"
            f"ALERTS_WEBHOOK_SECRET={settings.ALERTS_WEBHOOK_SECRET}\n"
            f"GCP_PROJECT_ID={settings.GCP_PROJECT_ID}\n"
            f"GCP_LOCATION={settings.GCP_LOCATION}\n"
            f"GEMINI_MODEL={settings.GEMINI_MODEL}\n"
            f"MCP_SERVER_URL={settings.MCP_SERVER_URL}\n"
            f"GOOGLE_GENAI_USE_VERTEXAI={'True' if settings.GOOGLE_GENAI_USE_VERTEXAI else 'False'}\n"
        )
        with open(".env", "w") as f:
            f.write(env_content)
            
        page.snack_bar = ft.SnackBar(ft.Text("Settings saved and .env updated!"))
        page.snack_bar.open = True
        page.update()

    return ft.Column([
        ft.Text("⚙️ Settings", size=24, weight="bold"),
        ft.Text("Configure your credentials and project defaults.", color=SUB_TEXT_COLOR),
        ft.Container(
            content=ft.Column([
                ft.Row([
                    gitlab_status,
                    ft.ElevatedButton("Connect to GitLab (OAuth)", icon=ft.Icons.LOCK_PERSON, on_click=start_gitlab_oauth),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Divider(color=BORDER_COLOR),
                gitlab_pat,
                gcp_project,
                gcp_location,
                gemini_model,
                ft.Button("Save Settings", icon=ft.Icons.SAVE, on_click=save_settings, bgcolor=ACCENT_COLOR, color="white"),
            ], spacing=20),
            padding=20,
            bgcolor=CARD_COLOR,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=10,
        )
    ], spacing=20, expand=True, scroll=ft.ScrollMode.ADAPTIVE)

# --- Main UI Entry Point ---

async def main(page: ft.Page):
    page.title = "RepoGuard"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.bgcolor = BG_COLOR
    
    content_container = ft.Container(expand=True, padding=20)

    async def navigate(e):
        index = e.control.selected_index
        if index == 0:
            content_container.content = await DashboardPage(page)
        elif index == 1:
            content_container.content = await ArchitectPage(page)
        elif index == 2:
            content_container.content = await SettingsPage(page)
        page.update()

    # Sidebar
    rail = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=100,
        bgcolor=CARD_COLOR,
        destinations=[
            ft.NavigationRailDestination(
                icon=ft.Icons.DASHBOARD_OUTLINED,
                selected_icon=ft.Icons.DASHBOARD,
                label="Dashboard",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.AUTO_AWESOME_OUTLINED,
                selected_icon=ft.Icons.AUTO_AWESOME,
                label="Architect",
            ),
            ft.NavigationRailDestination(
                icon=ft.Icons.SETTINGS_OUTLINED,
                selected_icon=ft.Icons.SETTINGS,
                label="Settings",
            ),
        ],
        on_change=lambda e: asyncio.create_task(navigate(e)),
    )

    page.add(
        ft.Row([
            rail,
            ft.VerticalDivider(width=1, color=BORDER_COLOR),
            content_container,
        ], expand=True)
    )

    # Initial Load
    content_container.content = await DashboardPage(page)
    page.update()
