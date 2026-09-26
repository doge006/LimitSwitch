"""The app's loopback HTTP servers.

The standard server looks up the machine's name when it starts (socket.getfqdn). On macOS that
lookup can go out to the local network, which shows the "find devices on local networks"
prompt. These servers only ever listen on 127.0.0.1, so they skip it."""
from http.server import ThreadingHTTPServer
import socketserver


class LocalServer(ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
