import argparse
import json
import sys

from .engine import Engine
from .i18n import tr
from .storage import YitoError, mutation, root_path
from .error_tracking import capture, flush, resume, report_threads
from . import diagnostics


def pause_owned_exit(exit_code):
    print(f'La aplicación terminó con el código {exit_code}.')
    try:
        input('Pulsa Intro para cerrar esta pestaña.')
    except (EOFError, KeyboardInterrupt):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(prog='mito', description='Tu entorno de desarrollo en una carpeta')
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('ui', help='Abrir la interfaz Textual').add_argument('--profile')
    commands.add_parser('start', help='Abrir MITO en Windows Terminal portable').add_argument('--profile')
    commands.add_parser('bootstrap', help='Preparar herramientas privadas')
    commands.add_parser('installed', help='Listar todos los componentes instalados')
    commands.add_parser('posthog-check', help='Enviar una excepción de diagnóstico y mostrar la respuesta HTTP')
    commands.add_parser('logs', help='Mostrar las carpetas de diagnóstico local')
    pocketool = commands.add_parser('pocketool-run', help='Ejecutar un comando Pocketool en una pestaña de MITO')
    pocketool.add_argument('--profile', required=True)
    pocketool.add_argument('--package', required=True)
    pocketool.add_argument('--entry', required=True)
    pocketool.add_argument('--owned', action='store_true', help=argparse.SUPPRESS)
    pocketool.add_argument('--arguments64', help=argparse.SUPPRESS)
    pocketool.add_argument('arguments', nargs=argparse.REMAINDER)
    host = commands.add_parser('host-run', help='Abrir un ejecutable externo con un profile de MITO')
    host.add_argument('identity')
    host.add_argument('--profile', required=True)
    host_mode = host.add_mutually_exclusive_group()
    host_mode.add_argument('--coupled', action='store_true')
    host_mode.add_argument('--owned', action='store_true', help=argparse.SUPPRESS)
    host.add_argument('arguments', nargs='*')
    commands.add_parser('doctor', help='Comprobar runtime y estado')
    mcp = commands.add_parser('mcp-server', help=argparse.SUPPRESS)
    mcp.add_argument('--pipe', required=True)
    mcp.add_argument('--owner', type=int, required=True)
    mcp.add_argument('--session', required=True)
    commands.add_parser('migrate-storage', help='Convertir instalaciones antiguas a rutas estables, con las aplicaciones cerradas')
    bucket = commands.add_parser('bucket').add_subparsers(dest='action', required=True)
    imp = bucket.add_parser('import')
    imp.add_argument('name')
    imp.add_argument('source')
    refresh = bucket.add_parser('refresh')
    refresh.add_argument('name', nargs='?')
    search = commands.add_parser('search')
    search.add_argument('query', nargs='?', default='')
    search.add_argument('--compatible', action='store_true')
    profiles = commands.add_parser('profile').add_subparsers(dest='action', required=True)
    create = profiles.add_parser('create')
    create.add_argument('name')
    create.add_argument('--workspace')
    profiles.add_parser('delete').add_argument('name')
    export = profiles.add_parser('export')
    export.add_argument('name')
    export.add_argument('output')
    imp_profile = profiles.add_parser('import')
    imp_profile.add_argument('source')
    imp_profile.add_argument('--name', required=True)
    profiles.add_parser('list')
    show = profiles.add_parser('show')
    show.add_argument('name')
    install = commands.add_parser('install')
    install.add_argument('app')
    install.add_argument('--profile')
    for action in ('activate', 'deactivate'):
        toggle = commands.add_parser(action)
        toggle.add_argument('installation', help='ID que muestra mito installed')
        toggle.add_argument('--profile', required=True)
    shell = commands.add_parser('shell')
    shell.add_argument('--profile', required=True)
    shell.add_argument('--type', choices=['cmd', 'powershell'], default='cmd')
    shell.add_argument('arguments', nargs=argparse.REMAINDER)
    terminal = commands.add_parser('terminal')
    terminal.add_argument('--profile', required=True)
    terminal.add_argument('--type', choices=['cmd', 'powershell'], default='cmd')
    terminal.add_argument('--new', action='store_true')
    run = commands.add_parser('run')
    run.add_argument('--profile', required=True)
    run.add_argument('--app', required=True)
    run.add_argument('--bin')
    run.add_argument('--shortcut')
    run_mode = run.add_mutually_exclusive_group()
    run_mode.add_argument('--coupled', action='store_true')
    run_mode.add_argument('--owned', action='store_true', help=argparse.SUPPRESS)
    run.add_argument('arguments', nargs=argparse.REMAINDER)
    app_link = commands.add_parser('app-shortcut', help='Crear un acceso de aplicación en el escritorio')
    app_link.add_argument('installation')
    app_link.add_argument('--profile', required=True)
    app_link.add_argument('--badge')
    app_link.add_argument('--color')
    app_link.add_argument('--shortcut')
    app_link.add_argument('--coupled', action='store_true')
    app_link.add_argument('arguments', nargs='*')
    link = commands.add_parser('shortcut', help='Acceso a MITO en el escritorio')
    link.add_argument('--profile', required=True)
    link.add_argument('--badge')
    link.add_argument('--color')
    release = commands.add_parser('release', help='Incrementar versión y publicar en GitHub')
    release.add_argument('kind', nargs='?', choices=['mayor', 'minor', 'hotfix'],
                         help='mayor: X.0.1; minor: M.X.1; hotfix: M.m.X (defecto)')
    release.add_argument('--local', action='store_true', help='Sólo generar ZIP local, sin incrementar ni publicar')
    release.add_argument('--output', help='Directorio de salida (defecto: releases)')
    args = parser.parse_args(argv)
    if args.command == 'release' and args.local and args.kind is not None:
        parser.error('--local no admite mayor, minor ni hotfix: empaqueta la versión actual')
    root = instance = root_path()
    previous_thread_hook = None
    try:
        engine = Engine(root)
        root = engine.root
        previous_thread_hook = report_threads(root)
        if args.command == 'logs':
            for folder in diagnostics.locations(root):
                print(folder)
            return 0
        diagnostics.write(root, 'info', 'cli.' + (args.command or 'ui'), 'Command started', runtime=diagnostics.environment())
        if args.command in (None, 'ui', 'start', 'bootstrap'):
            # Sólo bloquea abrir el gestor y preparar herramientas; las apps,
            # shims y MCP ya instalados siguen funcionando hasta mover la carpeta.
            from .path_limit import problem
            if message := problem(instance):
                diagnostics.write(root, 'error', 'startup.path-limit', message)
                print(f'\nMITO: {message}\n', file=sys.stderr)
                try:
                    input(tr('Pulsa Intro para cerrar'))
                except (EOFError, KeyboardInterrupt):
                    pass
                return 1
        if args.command == 'posthog-check':
            from .error_tracking import check
            try:
                status = check(engine.root)
            except Exception as exc:
                print(f'PostHog: envío fallido ({type(exc).__name__}): {exc}', file=sys.stderr)
                return 1
            print(f'PostHog: HTTP {status}. Excepción de diagnóstico enviada. '
                  'Comprueba el evento $exception con mito_diagnostic=true en tu proyecto; '
                  'la respuesta HTTP no confirma su procesamiento en Error Tracking.')
            return 0
        if args.command in (None, 'ui'):
            # Only the long-lived manager retries pending events: in shims such as git
            # the exit flush would wait for every retry on each invocation.
            resume(root)
        engine.initialize()
        if args.command in (None, 'ui', 'start'):
            # Nunca se borra el último profile; sólo el primer arranque puede no tener ninguno.
            engine.ensure_default_profile()
        if args.command != 'bootstrap':
            # Instancia movida o copiada: se repara una vez, antes de construir entornos.
            from .relocation import check as check_relocation, save_notice
            messages = []
            def relocation_log(message):
                diagnostics.write(root, 'info', 'relocation', message)
                messages.append(message)
                if args.command not in (None, 'ui'):
                    print(message)
            try:
                check_relocation(engine, relocation_log)
            except Exception as exc:
                # Nunca impide arrancar; se reintenta en el siguiente arranque.
                capture(root, exc, operation='relocation')
                relocation_log(tr('No se pudo completar la revisión del traslado de MITO: {p1}', p1=exc))
            if messages:
                # La UI puede ejecutarse en otro proceso (start → Terminal): la lee al abrirse.
                save_notice(root, messages)
        if args.command in ('shell', 'mcp-server') or getattr(args, 'owned', False):
            from .terminal_identity import apply_safely
            apply_safely(root)
        if args.command == 'mcp-server':
            from .mcp_server import run
            return run(engine, args.pipe, args.owner, args.session)
        if args.command == 'bootstrap':
            import subprocess
            return subprocess.call([sys.executable, '-B', '-I', '-X', 'utf8',
                str(engine.root/'core/bootstrap.py'), '--target', str(engine.root/'core/tools/python')])
        if args.command in (None, 'ui'):
            from .ui import YitoApp
            YitoApp(engine, getattr(args, 'profile', None)).run()
        elif args.command == 'start':
            from .terminal import launch
            return launch(engine, selected_profile=args.profile)
        elif args.command == 'host-run':
            from .host_apps import launch
            from .network import intercept
            intercept(engine.root, engine.environment(args.profile))
            result = launch(engine, args.profile, args.identity, args.arguments,
                coupled=args.coupled, owned=args.owned)
            if args.owned:
                pause_owned_exit(result)
            return result if args.owned else 0
        elif args.command == 'migrate-storage':
            from .storage_migration import migrate
            migrate(engine)
        elif args.command == 'installed':
            for install_id, lock in engine.installed().items():
                print(f'{install_id:65} {lock["version"]}')
        elif args.command in ('activate', 'deactivate'):
            engine.set_active(args.profile, args.installation, args.command == 'activate')
        elif args.command == 'shell':
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            return engine.shell(args.profile, arguments, args.type)
        elif args.command == 'doctor':
            import PIL
            from importlib.metadata import version
            print(f'MITO: {engine.root}\nPython: {sys.version.split()[0]} | Pillow: {PIL.__version__} | Textual: {version("textual")}')
            print(f'Home único: {engine.root / "home"}')
            from importlib.metadata import PackageNotFoundError
            try:
                print(f'MCP SDK: {version("mcp")} | activación manual; sin servidor al iniciar')
            except PackageNotFoundError:
                print('MCP SDK: falta; ejecuta mito.cmd bootstrap antes de habilitar MCP')
            for tool in ('windows-terminal', '7zip'):
                from .storage import read_json
                marker = engine.root / 'core/tools' / tool / '.mito-tool.json'
                print(f'{tool}: {read_json(marker)["version"]}' if marker.exists() else f'Falta {tool}; ejecuta bootstrap')
            print(f'Catálogo: {sum(1 for _ in engine.catalog.all())} apps | Profiles: {len(engine.profiles())}')
            for name in engine.profiles():
                engine.environment(name)
                print(f'OK profile {name}')
        elif args.command == 'bucket':
            with mutation(engine.root):
                if args.action == 'import':
                    print(f'{engine.catalog.import_bucket(args.name, args.source)} manifiestos importados')
                else:
                    sources = engine.catalog.sources()
                    if args.name:
                        if args.name not in sources:
                            raise YitoError('Bucket no configurado')
                        sources = {args.name: sources[args.name]}
                    for name, source in sources.items():
                        print(f'{name}: {engine.catalog.import_bucket(name, source)} manifiestos')
        elif args.command == 'search':
            from .search import rank_entries
            entries = [(key, m, engine.catalog.compatibility(key, m)) for key, m in engine.catalog.all()]
            for key, manifest, status in rank_entries(entries, args.query):
                if not args.compatible or status == 'Compatible':
                    print(f'{key:44} {str(manifest.get("version", "?")):20} {status}')
        elif args.command == 'profile':
            if args.action == 'create':
                print(f'Profile creado: {engine.create_profile(args.name, args.workspace)}')
            elif args.action == 'delete':
                engine.delete_profile(args.name)
                print(f'Profile eliminado: {args.name}')
            elif args.action == 'export':
                from .profile_transfer import export_profile
                print(export_profile(engine, args.name, args.output))
            elif args.action == 'import':
                from .profile_transfer import import_profile
                def ask(lines):
                    print('\n'.join(lines))
                    try:
                        return input('¿Continuar? [s/N] ').strip().lower() == 's'
                    except (EOFError, KeyboardInterrupt):
                        return False
                def review(records, items=None):
                    lines = [f'{r["key"]} {r["version"]}' + (' (retenida)' if r.get('pinned') else '')
                             for r in records.values()]
                    states = {'new': 'nuevo', 'same': 'idéntico, se omite', 'different': 'ya existe con otro contenido'}
                    if items:
                        lines.append('Archivos de configuración:')
                        lines += [f'  {item["target"]} · ' + ('propio del nuevo profile' if item['own'] else states[item['state']])
                                  for item in items]
                    if not ask(lines):
                        return False
                    if items is None:
                        return True
                    # Archivo a archivo, nunca en bloque.
                    return [item['target'] for item in items if not item['own'] and item['state'] == 'different'
                            and ask([f'¿Sobrescribir {item["target"]}?'])]
                import_profile(engine, args.source, args.name, confirm_plan=review,
                    confirm_unverified=lambda downloads: ask(['Descargas sin hash:', *downloads]))
            elif args.action == 'list':
                print('\n'.join(engine.profiles()) or 'Todavía no hay profiles')
            else:
                print(json.dumps(engine.profile(args.name), ensure_ascii=False, indent=2))
        elif args.command == 'install':
            def confirm(downloads):
                print('Descargas sin hash: no se podrá comprobar su integridad.')
                print('\n'.join(downloads))
                try:
                    return input('¿Descargar e instalar? [s/N] ').strip().lower() == 's'
                except (EOFError, KeyboardInterrupt):
                    return False
            engine.install(args.app, args.profile, confirm_unverified=confirm)
        elif args.command == 'terminal':
            from .terminal import launch
            return launch(engine, args.profile, args.type)
        elif args.command == 'pocketool-run':
            from .pocketool_execution import decoded_arguments, execute, launch
            arguments = decoded_arguments(args.arguments64) if args.arguments64 else (
                args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments)
            if args.owned:
                result = execute(engine, args.profile, args.package, args.entry, arguments)
                pause_owned_exit(result)
                return result
            return launch(engine, args.profile, args.package, args.entry, arguments)
        elif args.command == 'run':
            if not args.bin:
                from .network import intercept
                intercept(engine.root, engine.environment(args.profile))
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            result = engine.launch(args.profile, args.app, args.bin, arguments,
                coupled=args.coupled, owned=args.owned, shortcut=args.shortcut)
            if args.owned:
                pause_owned_exit(result)
            return result
        elif args.command == 'release':
            from .release import build_release
            if args.local:
                print(build_release(engine, args.output))
            else:
                from .publish import publish
                print(publish(engine, args.output, kind=args.kind or 'hotfix'))
        elif args.command == 'shortcut':
            from .shortcuts import mito_link
            with mutation(engine.root):
                print(mito_link(engine, args.profile, args.badge, color=args.color))
        elif args.command == 'app-shortcut':
            from .shortcuts import application_link
            with mutation(engine.root):
                print(application_link(engine, args.profile, args.installation, args.badge,
                    coupled=args.coupled, arguments=args.arguments, shortcut=args.shortcut, color=args.color))
        return 0
    except (YitoError, OSError, ValueError) as exc:
        capture(root, exc, operation='cli.' + (args.command or 'ui'))
        print(f'MITO: {exc}', file=sys.stderr)
        if getattr(args, 'owned', False):
            pause_owned_exit(1)
        return 1
    except KeyboardInterrupt:
        print('Operación interrumpida', file=sys.stderr)
        return 130
    except Exception as exc:
        capture(root, exc, handled=False, operation='cli.' + (args.command or 'ui'))
        raise
    finally:
        flush()
        if previous_thread_hook is not None:
            import threading
            threading.excepthook = previous_thread_hook


if __name__ == '__main__':
    raise SystemExit(main())
