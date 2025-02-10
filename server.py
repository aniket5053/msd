from http import server
import socketserver
import logging
import json

class WebServer:
    def __init__(self, camera_processor, sensor_manager):
        self.camera_processor = camera_processor
        self.sensor_manager = sensor_manager

    class StreamingHandler(server.BaseHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            self.server_ref = kwargs.pop('server_ref')
            super().__init__(*args, **kwargs)

        def do_GET(self):
            if self.path == '/':
                self.send_response(301)
                self.send_header('Location', '/index.html')
                self.end_headers()
            elif self.path == '/index.html':
                self.serve_static('templates/index.html', 'text/html')
            elif self.path == '/stream.mjpg':
                self.handle_stream()
            elif self.path == '/count':
                self.send_json({'count': self.server_ref.camera_processor.output.get_red_count()})
            elif self.path == '/sensors':
                temp, hum = self.server_ref.sensor_manager.read_sensors()
                self.send_json({'temperature': temp, 'humidity': hum})
            elif self.path == '/graph-data':
                self.send_json(self.server_ref.sensor_manager.get_history())
            else:
                self.send_error(404)
                self.end_headers()

        def serve_static(self, path, content_type):
            try:
                with open(path, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', len(content))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error(404)

        def send_json(self, data):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))

        def handle_stream(self):
            try:
                self.send_response(200)
                self.send_header('Age', 0)
                self.send_header('Cache-Control', 'no-cache, private')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
                self.end_headers()

                while True:
                    with self.server_ref.camera_processor.output.condition:
                        self.server_ref.camera_processor.output.condition.wait()
                        frame = self.server_ref.camera_processor.output.frame

                    processed_frame = self.server_ref.camera_processor.process_frame(frame)
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(processed_frame))
                    self.end_headers()
                    self.wfile.write(processed_frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning('Client disconnected: %s', str(e))

    class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
        allow_reuse_address = True
        daemon_threads = True