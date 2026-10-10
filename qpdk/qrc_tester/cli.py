"""
Standalone CLI for QRC Mock Server
"""

import click
from rich.console import Console

from .server import QRCMockServer

console = Console()


@click.command()
@click.option("--port", "-p", default=1710, help="Port to listen on")
@click.option("--host", "-h", "host", default="127.0.0.1", help="Host to bind to")
@click.option("--record", "-r", type=click.Path(), help="Record interactions to file")
@click.option("--playback", type=click.Path(exists=True), help="Playback recorded responses")
@click.option("--add-control", "-c", "controls", multiple=True,
              help="Add control (format: name=value)")
@click.option("--add-component", multiple=True,
              help="Add component (format: name:ctrl1,ctrl2)")
@click.option("--user", "-u", "users", multiple=True,
              help="Add valid user (format: username:password)")
def main(port: int, host: str, record: str | None, playback: str | None,
         controls: tuple[str, ...], users: tuple[str, ...],
         add_component: tuple[str, ...]):
    """Start a mock QRC protocol server.

    Simulates a Q-SYS Core for integration testing.

    Examples:
        # Basic server
        qpdk-qrc

        # With controls
        qpdk-qrc -c "Volume=0.5" -c "Mute=0"

        # With component
        qpdk-qrc --add-component "Mixer:gain.1,gain.2,mute"

        # With authentication
        qpdk-qrc -u "admin:password"

        # Record interactions
        qpdk-qrc --record session.json

        # Playback recorded responses
        qpdk-qrc --playback recorded.json
    """
    server = QRCMockServer(host=host, port=port)

    # Add controls
    for ctrl_spec in controls:
        if "=" in ctrl_spec:
            name, value = ctrl_spec.split("=", 1)
            try:
                server.add_control(name, float(value))
            except ValueError:
                server.add_control(name, 0.0, value)
        else:
            server.add_control(ctrl_spec)

    # Add components
    for comp_spec in add_component:
        if ":" in comp_spec:
            name, ctrls = comp_spec.split(":", 1)
            ctrl_list = [c.strip() for c in ctrls.split(",")]
            server.add_component(name, ctrl_list)

    # Add users
    for user_spec in users:
        if ":" in user_spec:
            username, password = user_spec.split(":", 1)
            server.set_credentials(username, password)
        else:
            server.set_credentials(user_spec, "")

    # Load playback
    if playback:
        server.load_playback(playback)
        console.print(f"[blue]Loaded playback from: {playback}[/blue]")

    # Print status
    console.print("[green]QRC Mock Server[/green]")
    console.print(f"  Host: {host}")
    console.print(f"  Port: {port}")

    if server.state.controls:
        console.print(f"  Controls: {len(server.state.controls)}")
    if server.state.components:
        console.print(f"  Components: {len(server.state.components)}")

    if record:
        console.print(f"  Recording to: {record}")

    console.print("\nPress Ctrl+C to stop\n")

    try:
        server.run(record_to=record)
    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped[/yellow]")

    if record and server.state.recording:
        console.print(f"[blue]Saved {len(server.state.recording)} interactions to {record}[/blue]")


if __name__ == "__main__":
    main()
