"""Desktop-only monitoring extension; ordinary H5/CLI remains unchanged."""
import secrets
from urllib.parse import urlsplit
from .web import WebApp, LocalServer, Handler, WebError


class ManagedWebApp(WebApp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.monitor = None
        self.paused = False

    def submit(self, sid, payload):
        with self.lock:
            if self.paused:
                raise WebError('后台管理已暂停接收新任务，请在管理窗口中恢复。', 409)
            return super().submit(sid, payload)


class MonitorHandler(Handler):
    def route(self, method):
        path = urlsplit(self.path).path
        if path not in ('/api/monitor', '/api/monitor/show'):
            return super().route(method)
        try:
            self.check_origin()
            if not secrets.compare_digest(self.headers.get('X-Agent-Token', ''), self.app.token):
                raise WebError('Invalid session token', 403)
            if method == 'GET' and path == '/api/monitor':
                return self.respond(self.app.monitor.snapshot() if self.app.monitor else {'status': 'not_started'})
            if method == 'POST' and path == '/api/monitor/show':
                self.read_json()
                if self.app.monitor:
                    self.app.monitor.focus.set()
                return self.respond({'ok': self.app.monitor is not None})
            raise WebError('Method not allowed', 405)
        except WebError as e:
            self.close_connection = True
            self.respond({'error': str(e)}, e.code)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ManagedServer(LocalServer):
    def finish_request(self, request, client_address):
        MonitorHandler(request, client_address, self)
