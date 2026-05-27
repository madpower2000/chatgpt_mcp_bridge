"""Entry point for: python -m chatgpt_mcp_bridge"""
import sys
import os

# Ensure plugins dir is in path
plugins_dir = os.path.join(os.path.expanduser("~"), ".hermes", "plugins")
if plugins_dir not in sys.path:
    sys.path.insert(0, plugins_dir)

if "cli" in sys.argv[1:]:
    # Remove 'cli' from args and dispatch
    sys.argv = ["chatgpt_mcp_bridge"] + [a for a in sys.argv[1:] if a != "cli"]
    from chatgpt_mcp_bridge.cli import main
    main()
else:
    from chatgpt_mcp_bridge import run_server
    run_server()
