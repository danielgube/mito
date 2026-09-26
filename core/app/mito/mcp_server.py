"""MCP HTTP process hosted by the dedicated Windows Terminal tab."""
import asyncio
from contextlib import asynccontextmanager
import json
import socket
import threading


def make_app(tools, port):
    from mcp.server.lowlevel import Server
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp.types import Tool, CallToolResult, TextContent
    from starlette.applications import Starlette
    from starlette.routing import Route
    from . import __version__
    server = Server('MITO', version=__version__, instructions=
        'Use explicit profiles and bucket/app IDs. Commands run with Windows user permissions, not in a sandbox. '
        'Install and activate separately. Poll jobs and their bounded output. Unsigned downloads need human approval in MITO.')

    @server.list_tools()
    async def list_tools():
        return [Tool(**item) for item in tools.definitions()]

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            return await asyncio.to_thread(tools.call, name, arguments)
        except Exception as exc:
            from .jobs import error_info
            error = error_info(exc)
            return CallToolResult(isError=True, content=[TextContent(type='text', text=json.dumps(error))], structuredContent={'error': error})

    manager = StreamableHTTPSessionManager(server, json_response=True,
        max_request_body_size=1024*1024, max_sessions=32, session_idle_timeout=1800,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[f'127.0.0.1:{port}', f'localhost:{port}'], allowed_origins=[]))

    class Endpoint:
        async def __call__(self, scope, receive, send):
            if tools.jobs.stopping:
                from starlette.responses import Response
                await Response('MCP stopping', status_code=503)(scope, receive, send)
            else:
                await manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app):
        async with manager.run(): yield

    return Starlette(routes=[Route('/mcp', Endpoint(), methods=['GET', 'POST', 'DELETE'])], lifespan=lifespan)


def run(engine, pipe, owner, session):
    from multiprocessing.connection import Client
    from .processes import ProcessHandle
    from .mcp_service import send, receive
    from .storage import YitoError
    owner_handle = ProcessHandle(owner)
    connection = Client(pipe, family='AF_PIPE')
    send_lock = threading.Lock()
    def notify(message):
        with send_lock:
            try: send(connection, message)
            except (OSError, EOFError): stopping.set()
    stopping = threading.Event()
    sock = None; jobs = None
    try:
        if not connection.poll(15): raise RuntimeError('Manager handshake timed out')
        config = receive(connection)
        if config.get('type') != 'start' or config.get('session') != session:
            raise RuntimeError('Invalid manager session')
        if not owner_handle.alive(): return 1
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(('127.0.0.1', config['port'])); sock.listen(128)
        port = sock.getsockname()[1]
        from .jobs import Jobs
        from .mcp_tools import Tools
        import uvicorn
        jobs = Jobs(engine, session, notify)
        app = make_app(Tools(engine, jobs), port)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
            log_level='warning', access_log=False, limit_concurrency=64, timeout_graceful_shutdown=3))

        def controls():
            try:
                while owner_handle.alive() and not stopping.is_set():
                    if not connection.poll(.2): continue
                    message = receive(connection)
                    if message.get('type') == 'stop': break
                    try:
                        if message.get('type') == 'approve': jobs.approve(message['job_id'], message['answer'])
                        if message.get('type') == 'cancel': jobs.cancel(message['job_id'])
                        if message.get('type') == 'output':
                            notify({'type': 'output', 'job': jobs.get(message['job_id']).snapshot(),
                                'output': jobs.read(message['job_id'], message.get('stream', 'stdout'), message.get('cursor', 0))})
                    except (KeyError, ValueError, OSError, YitoError) as exc:
                        notify({'type': 'error', 'message': str(exc)})
            except (OSError, EOFError): pass
            finally: stopping.set()
        threading.Thread(target=controls, name='mito-mcp-owner', daemon=True).start()

        async def serve():
            task = asyncio.create_task(server.serve(sockets=[sock]))
            while not server.started and not task.done() and not stopping.is_set(): await asyncio.sleep(.05)
            if server.started:
                print(f'MITO MCP | http://127.0.0.1:{port}/mcp\nLocal only. Close this tab to stop MCP.', flush=True)
                notify({'type': 'ready', 'port': port})
            while not stopping.is_set() and not task.done(): await asyncio.sleep(.1)
            jobs.stop()
            await asyncio.to_thread(jobs.close)
            server.should_exit = True
            await task
        asyncio.run(serve())
        return 0
    except Exception as exc:
        notify({'type': 'error', 'message': str(exc)})
        print(f'MITO MCP: {exc}', flush=True)
        return 1
    finally:
        stopping.set()
        if jobs: jobs.close()
        if sock: sock.close()
        connection.close(); owner_handle.close()
