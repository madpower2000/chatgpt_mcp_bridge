"""
Entry point for chatgpt_mcp_bridge.

Usage:
    python -m chatgpt_mcp_bridge              # Start standalone MCP server
    python -m chatgpt_mcp_bridge --port 9101  # Custom port
    python -m chatgpt_mcp_bridge cli status   # CLI commands
"""

import sys

def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage:")
        print("  python -m chatgpt_mcp_bridge              Start MCP server")
        print("  python -m chatgpt_mcp_bridge --port PORT  Start on custom port")
        print("  python -m chatgpt_mcp_bridge cli <cmd>    CLI commands")
        print()
        print("CLI commands: status, install, start, stop, uninstall, tunnel-url")
        print()
        print("For CLI help, run: python -m chatgpt_mcp_bridge cli --help")
        sys.exit(0)

    # If first arg is 'cli', dispatch to CLI
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        from .cli import main as cli_main
        cli_main()
    else:
        from . import run_server
        run_server()

if __name__ == "__main__":
    main()
