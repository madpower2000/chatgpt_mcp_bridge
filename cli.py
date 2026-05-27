"""
CLI entry point for chatgpt_mcp_bridge.

Usage:
    python -m chatgpt_mcp_bridge cli status
    python -m chatgpt_mcp_bridge cli install [--dry-run] [--tunnel-mode quick|named] [--named-tunnel NAME]
    python -m chatgpt_mcp_bridge cli start
    python -m chatgpt_mcp_bridge cli stop
    python -m chatgpt_mcp_bridge cli uninstall
    python -m chatgpt_mcp_bridge cli tunnel-url
"""

import argparse
import json
import subprocess
import sys
import os

def _ensure_pythonpath():
    """Ensure ~/.hermes/plugins is in PYTHONPATH."""
    plugins_dir = os.path.join(os.path.expanduser("~"), ".hermes", "plugins")
    if plugins_dir not in sys.path:
        sys.path.insert(0, plugins_dir)


def cmd_status(args):
    """Show bridge status and systemd service states."""
    _ensure_pythonpath()
    from chatgpt_mcp_bridge import services

    print("=" * 60)
    print("chatgpt_mcp_bridge status")
    print("=" * 60)

    # Bridge service
    result = services.check_service("chatgpt-mcp-bridge")
    status_icon = "\u2705" if result["is_active"] else "\u274c"
    print(f"\n{status_icon} Bridge service: {result['name']}")
    if result["is_active"]:
        print(f"   PID: {result.get('pid', 'N/A')}")
        print(f"   Memory: {result.get('memory', 'N/A')}")
    else:
        if result.get("error"):
            print(f"   Error: {result['error']}")
        print(f"   Result: {result.get('result', 'unknown')}")

    # Tunnel service
    result = services.check_service("chatgpt-mcp-cloudflared")
    status_icon = "\u2705" if result["is_active"] else "\u274c"
    print(f"\n{status_icon} Tunnel service: {result['name']}")
    if result["is_active"]:
        print(f"   PID: {result.get('pid', 'N/A')}")
        print(f"   Memory: {result.get('memory', 'N/A')}")
    else:
        if result.get("error"):
            print(f"   Error: {result['error']}")
        print(f"   Result: {result.get('result', 'unknown')}")

    # Check local MCP endpoint
    try:
        import subprocess as sp
        check = sp.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "http://127.0.0.1:9100/mcp",
             "-H", "Content-Type: application/json",
             "-H", "Accept: application/json, text/event-stream",
             "-d", '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'],
            capture_output=True, text=True, timeout=5
        )
        code = check.stdout.strip()
        if code == "200":
            print(f"\n✅ MCP endpoint: http://127.0.0.1:9100/mcp (200 OK)")
        else:
            print(f"\n❌ MCP endpoint: http://127.0.0.1:9100/mcp ({code})")
    except Exception as e:
        print(f"\n❌ MCP endpoint: http://127.0.0.1:9100/mcp (unreachable: {e})")

    print()


def cmd_install(args):
    """Install systemd services."""
    _ensure_pythonpath()
    from chatgpt_mcp_bridge import services

    result = services.install_services(
        mode="user",
        tunnel_mode=args.tunnel_mode,
        host=args.host,
        port=args.port,
        named_tunnel=args.named_tunnel,
        dry_run=args.dry_run,
        enable=not args.dry_run,
    )

    data = json.loads(result)
    if not data["ok"]:
        print(f"ERROR: {data.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)

    if data.get("dry_run"):
        print("DRY RUN — unit files not written.")
        print()

        for name, content in data.items():
            if name in ("bridge_unit", "cloudflared_unit"):
                svc_name = "bridge" if name == "bridge_unit" else "tunnel"
                print(f"--- {svc_name} unit (would be written) ---")
                print(content)
                print()
        return

    print("Installed systemd services:")
    print(f"  Bridge:  {data['paths']['bridge']}")
    print(f"  Tunnel:  {data['paths']['cloudflared']}")

    if data.get("enable_results"):
        for r in data["enable_results"]:
            status = "\u2705" if r["success"] else "\u274c"
            print(f"  {status} {r['service']} enabled")

    print()
    print("To start services:")
    print("  chatgpt_mcp_bridge start")
    print()
    print("To see logs:")
    print("  journalctl --user -u chatgpt-mcp-bridge.service -f")
    print("  journalctl --user -u chatgpt-mcp-cloudflared.service -f")
    print()


def cmd_start(args):
    """Start systemd services."""
    _ensure_pythonpath()
    from chatgpt_mcp_bridge import services

    result = services.start_services()
    data = json.loads(result)

    for svc, info in data.items():
        if svc == "hint":
            continue
        status = "\u2705" if info["success"] else "\u274c"
        print(f"{status} {svc}: {'started' if info['success'] else 'FAILED (exit ' + str(info['exit_code']) + ')'}")

    if data.get("hint"):
        print()
        print(data["hint"])
    print()


def cmd_stop(args):
    """Stop systemd services."""
    _ensure_pythonpath()
    from chatgpt_mcp_bridge import services

    result = services.stop_services()
    data = json.loads(result)

    for svc, info in data.items():
        if svc == "hint":
            continue
        status = "\u2705" if info["success"] else "\u274c"
        print(f"{status} {svc}: {'stopped' if info['success'] else 'FAILED (exit ' + str(info['exit_code']) + ')'}")
    print()


def cmd_uninstall(args):
    """Remove systemd services."""
    _ensure_pythonpath()
    from chatgpt_mcp_bridge import services

    # Stop services first
    stop_result = services.stop_services()
    stop_data = json.loads(stop_result)

    # Disable
    for svc in ["chatgpt-mcp-cloudflared.service", "chatgpt-mcp-bridge.service"]:
        out = subprocess.run(
            ["systemctl", "--user", "disable", svc],
            capture_output=True, text=True
        )
        if out.returncode == 0:
            print(f"\u2705 {svc} disabled")
        else:
            print(f"\u274c {svc} disable failed: {out.stderr.strip()}")

    # Remove unit files
    unit_dir = os.path.join(os.path.expanduser("~"), ".config", "systemd", "user")
    for svc in ["chatgpt-mcp-bridge.service", "chatgpt-mcp-cloudflared.service"]:
        path = os.path.join(unit_dir, svc)
        if os.path.exists(path):
            os.remove(path)
            print(f"\u2705 Removed {path}")
        else:
            print(f"  (not found: {path})")

    # Reload
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    print("\u2705 Reloaded systemd user manager")
    print()
def cmd_tunnel_url(args):
    """Show the Cloudflare tunnel URL (from journal logs)."""
    out = subprocess.run(
        ["journalctl", "--user", "-u", "chatgpt-mcp-cloudflared.service",
         "-n", "50", "--no-pager", "-q"],
        capture_output=True, text=True
    )

    for line in out.stdout.splitlines():
        if "trycloudflare.com" in line:
            if "https://" in line:
                url = "https://" + line.split("https://")[1].split()[0].rstrip("|").strip()
                print(url)
                return

    print("No tunnel URL found. Is the tunnel service running?", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="chatgpt_mcp_bridge",
        description="ChatGPT MCP Bridge — systemd service management CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    p_status = subparsers.add_parser("status", help="Show bridge and service status")

    # install
    p_install = subparsers.add_parser("install", help="Install systemd services")
    p_install.add_argument("--dry-run", action="store_true", help="Only render unit files")
    p_install.add_argument("--tunnel-mode", default="quick", choices=["quick", "named"],
                           help="Tunnel mode (default: quick)")
    p_install.add_argument("--named-tunnel", default="", help="Named tunnel name (required for named mode)")
    p_install.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    p_install.add_argument("--port", type=int, default=9100, help="Port (default: 9100)")

    # start
    subparsers.add_parser("start", help="Start bridge and tunnel services")

    # stop
    subparsers.add_parser("stop", help="Stop bridge and tunnel services")

    # uninstall
    subparsers.add_parser("uninstall", help="Remove systemd services")

    # tunnel-url
    subparsers.add_parser("tunnel-url", help="Show Cloudflare tunnel URL")

    args = parser.parse_args()

    cmds = {
        "status": cmd_status,
        "install": cmd_install,
        "start": cmd_start,
        "stop": cmd_stop,
        "uninstall": cmd_uninstall,
        "tunnel-url": cmd_tunnel_url,
    }

    cmds[args.command](args)


if __name__ == "__main__":
    main()
