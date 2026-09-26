"""Stdlib-only entry guard: diagnose import failures before the CLI can load."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / 'core/app'))
from mito import diagnostics


def main():
    try:
        from mito.__main__ import main as cli
    except Exception as exc:
        diagnostics.write(root, 'error', 'startup.import', diagnostics.message(root, exc),
                          runtime=diagnostics.environment(), exception=type(exc).__name__)
        try:
            from mito.error_tracking import capture, flush
            capture(root, exc, handled=False, operation='startup.import')
            flush()
        except Exception:
            pass
        print(f'MITO: {exc}\nLog: {diagnostics.locations(root)[0]}', file=sys.stderr)
        return 1
    return cli()


if __name__ == '__main__':
    raise SystemExit(main())
