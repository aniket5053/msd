import io
import logging
import socketserver
import json
import time
import threading
from collections import deque
from http import server
from threading import Condition, Lock
import cv2
import numpy as np
import board
import adafruit_sht4x
import adafruit_dotstar as dotstar
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

# Hardware Initialization
def initialize_hardware():
    """Initialize sensors and LEDs"""
    global sht, dots
    sht = adafruit_sht4x.SHT4x(board.I2C())
    dots = dotstar.DotStar(board.SCK, board.MOSI, 4, brightness=0.2)
    dots.fill((0, 0, 0))  # Start with LEDs off

# Global State Management
class AppState:
    """Thread-safe application state management"""
    def __init__(self):
        self.lock = Lock()
        self.sensor_data = deque(maxlen=100)
        self.streaming_active = False
        self.led_enable = False
        self.led_enable_time = 0
        self.red_count = 0

    def update_sensor_data(self, temp, hum):
        """Store new sensor readings"""
        with self.lock:
            if (not self.sensor_data or 
                time.time() - self.sensor_data[-1]['time'] >= 10 or
                abs(temp - self.sensor_data[-1]['temperature']) > 0.1 or
                abs(hum - self.sensor_data[-1]['humidity']) > 0.5):
                
                self.sensor_data.append({
                    "time": time.time(),
                    "temperature": temp,
                    "humidity": hum
                })

    def get_sensor_data(self):
        """Get copy of sensor data"""
        with self.lock:
            return list(self.sensor_data)

# Web Page Configuration
PAGE = """<html>...</html>"""  # Keep your original HTML/CSS/JS here

# Camera and Streaming
class VideoStreamer:
    """Manage camera streaming and frame processing"""
    def __init__(self):
        self.picam2 = Picamera2()
        self.output = None
        self.configure_camera()

    def configure_camera(self):
        """Set up camera configuration"""
        config = self.picam2.create_video_configuration({
            'size': (640, 640),
            'format': 'XRGB8888'
        })
        self.picam2.configure(config)
        self.output = StreamingOutput()

    def start_recording(self):
        """Start camera recording"""
        self.picam2.start_recording(JpegEncoder(), FileOutput(self.output))

    def stop_recording(self):
        """Stop camera recording"""
        self.picam2.stop_recording()

class StreamingOutput(io.BufferedIOBase):
    """Handle frame processing and output"""
    def __init__(self):
        self.frame = None
        self.condition = Condition()
        self.red_count = 0

    def write(self, buf):
        """Process frame and detect red objects"""
        img = cv2.imdecode(np.frombuffer(buf, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            # Red detection logic here
            pass
        # Rest of your frame processing code

    def get_red_count(self):
        return self.red_count

# Web Server Handlers
class StreamingHandler(server.BaseHTTPRequestHandler):
    """Handle HTTP requests and streaming"""
    def handle_api_request(self, app_state):
        """Process API endpoints"""
        if self.path == '/sensors':
            self.send_json(app_state.get_sensor_data())
        elif self.path == '/count':
            self.send_json({'count': self.server.stream_output.get_red_count()})
        elif self.path == '/stream_status':
            self.send_json({'streaming': app_state.streaming_active})
        else:
            self.send_error(404)

    def send_json(self, data):
        """Send JSON response"""
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def do_GET(self):
        """Handle GET requests"""
        if self.path == '/':
            self.redirect('/index.html')
        elif self.path in ['/index.html', '/index']:
            self.serve_page()
        elif self.path == '/stream.mjpg':
            self.handle_stream()
        else:
            self.handle_api_request(self.server.app_state)

    def do_POST(self):
        """Handle POST requests"""
        if self.path == '/toggle_stream':
            self.toggle_stream()
        else:
            self.send_error(404)

    def toggle_stream(self):
        """Handle stream toggle requests"""
        with self.server.app_state.lock:
            app_state = self.server.app_state
            app_state.streaming_active = not app_state.streaming_active
            
            if app_state.streaming_active:
                app_state.led_enable = True
                app_state.led_enable_time = time.time()
                dots.fill((255, 255, 255))
            else:
                app_state.led_enable = False
                dots.fill((0, 0, 0))
            
            self.send_json({'streaming': app_state.streaming_active})

    def redirect(self, location):
        """Redirect to specified location"""
        self.send_response(301)
        self.send_header('Location', location)
        self.end_headers()

    def serve_page(self):
        """Serve main HTML page"""
        content = PAGE.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', len(content))
        self.end_headers()
        self.wfile.write(content)

    def handle_stream(self):
        """Handle video streaming"""
        with self.server.app_state.lock:
            if not self.server.app_state.streaming_active:
                self.send_error(404)
                return

        self.send_stream_headers()
        try:
            while True:
                with self.server.stream_output.condition:
                    self.server.stream_output.condition.wait()
                    if not self.server.app_state.streaming_active:
                        break
                    frame = self.server.stream_output.frame
                self.send_frame(frame)
        except Exception as e:
            logging.warning('Streaming stopped: %s', str(e))

    def send_stream_headers(self):
        """Send streaming headers"""
        self.send_response(200)
        self.send_header('Age', '0')
        self.send_header('Cache-Control', 'no-cache, private')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
        self.end_headers()

    def send_frame(self, frame):
        """Send individual video frame"""
        self.wfile.write(b'--FRAME\r\n')
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', len(frame))
        self.end_headers()
        self.wfile.write(frame)
        self.wfile.write(b'\r\n')

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    """Custom HTTP server with shared state"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.app_state = AppState()
        self.streamer = VideoStreamer()
        self.stream_output = self.streamer.output

# Sensor Monitoring
def sensor_monitor(app_state):
    """Continuous sensor monitoring thread"""
    while True:
        try:
            temp = sht.temperature * 9/5 + 32  # Convert to Fahrenheit
            hum = sht.relative_humidity
            app_state.update_sensor_data(round(temp, 3), round(hum, 3))
            check_led_timeout(app_state)
        except Exception as e:
            logging.error("Sensor error: %s", e)
        time.sleep(1)

def check_led_timeout(app_state):
    """Check and handle LED timeout"""
    with app_state.lock:
        if app_state.led_enable and (time.time() - app_state.led_enable_time) >= 60:
            app_state.led_enable = False
            app_state.streaming_active = False
            dots.fill((0, 0, 0))

# Main Application
def main():
    """Main application setup and execution"""
    initialize_hardware()
    
    # Create and configure server
    server = StreamingServer(('', 7123), StreamingHandler)
    server.streamer.start_recording()
    
    # Start sensor thread
    sensor_thread = threading.Thread(
        target=sensor_monitor,
        args=(server.app_state,),
        daemon=True
    )
    sensor_thread.start()
    
    # Start web server
    try:
        server.serve_forever()
    finally:
        server.streamer.stop_recording()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()