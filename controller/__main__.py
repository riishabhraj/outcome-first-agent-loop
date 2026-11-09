import sys

if len(sys.argv) > 1 and sys.argv[1] in ("why", "view", "status", "audit", "lead"):
    from .view import cli
    cli(sys.argv[1:])
else:
    from .main import main
    main(sys.argv[1:])
