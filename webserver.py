import io
import logging
import socketserver
import json
import time
import threading
import os
import math
from collections import deque
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Condition, Lock, RLock
import cv2
import numpy as np
import board
import adafruit_sht4x
import adafruit_dotstar as dotstar
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

# Configuration
SNAPSHOT_ROOT = "snapshots"
STREAM_CONFIG = {"size": (640, 640), "format": "XRGB8888"}
STILL_CONFIG = {"size": (2304, 1746), "format": "XRGB8888"}
SNAPSHOT_INTERVAL = 60  # seconds
PORT = 7123

# Initialize hardware
os.makedirs(SNAPSHOT_ROOT, exist_ok=True)
sht = adafruit_sht4x.SHT4x(board.I2C())
dots = dotstar.DotStar(board.SCK, board.MOSI, 4, brightness=0.2)

# Global state
streaming_enabled = True
sensor_data = deque(maxlen=86400)
data_lock = Lock()
camera_lock = RLock()

PAGE = """<!DOCTYPE html>
<html>
<head>
    <title>HiveHealth Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Roboto+Condensed:wght@400;700&family=Honeybee&display=swap" rel="stylesheet">
    <style>
        :root {
            --honey-gold: #FFB347;
            --hive-brown: #6B4423;
            --comb-yellow: #F4D03F;
            --healthy-green: #82C341;
            --alert-red: #E74C3C;
        }
        body {
            margin: 0;
            padding: 0;
            background: linear-gradient(45deg, #fff5e6, #fff);
            font-family: 'Roboto Condensed', sans-serif;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        .header {
            background: var(--hive-brown);
            padding: 1rem;
            color: var(--comb-yellow);
            text-align: center;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .video-container {
            position: relative;
            width: 100%;
            padding-top: 56.25%; /* 16:9 aspect ratio */
            background: var(--hive-brown);
            border-radius: 10px;
            overflow: hidden;
        }
        .video-feed {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
        }
        .metrics {
            display: flex;
            justify-content: center;
            gap: 20px;
            margin: 20px 0;
        }
        .metric {
            padding: 15px;
            background: white;
            border-radius: 10px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            min-width: 150px;
            text-align: center;
        }
        .chart-container {
            background: white;
            padding: 20px;
            border-radius: 10px;
            margin: 20px 0;
        }
        .snapshot-link {
            display: inline-block;
            padding: 10px 20px;
            background: var(--comb-yellow);
            color: white;
            text-decoration: none;
            border-radius: 5px;
            transition: background 0.3s;
        }
        .snapshot-link:hover {
            background: #f39c12;
        }
        button {
            padding: 10px 20px;
            background: var(--healthy-green);
            border: none;
            border-radius: 5px;
            color: white;
            cursor: pointer;
        }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
        let chart;
        function initChart() {
            const ctx = document.getElementById('chart').getContext('2d');
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    datasets: [{
                        label: 'Temperature (°F)',
                        borderColor: '#FFB347',
                        tension: 0.3
                    }, {
                        label: 'Humidity (%)',
                        borderColor: '#3498db',
                        tension: 0.3
                    }]
                },
                options: {
                    responsive: true,
                    scales: {
                        x: { type: 'time', time: { tooltipFormat: 'HH:mm' } },
                        y: { beginAtZero: true }
                    }
                }
            });
        }
        function updateMetrics() {
            fetch('/sensors')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('temp').textContent = data.temperature.toFixed(1);
                    document.getElementById('hum').textContent = data.humidity.toFixed(1);
                    document.getElementById('count').textContent = data.count;
                    
                    chart.data.datasets[0].data = data.history.map(d => ({
                        x: d.time * 1000,
                        y: d.temperature
                    }));
                    chart.data.datasets[1].data = data.history.map(d => ({
                        x: d.time * 1000,
                        y: d.humidity
                    }));
                    chart.update();
                });
        }
        function toggleStream() {
            fetch('/toggle')
                .then(() => updateMetrics());
        }
        setInterval(updateMetrics, 1000);
        window.onload = initChart;
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🐝 Hive Health Monitor</h1>
        </div>
        
        <div class="video-container">
            <img class="video-feed" src="stream.mjpg" />
        </div>
        
        <div class="metrics">
            <div class="metric">
                <h3>Temperature</h3>
                <div id="temp">--</div>°F
            </div>
            <div class="metric">
                <h3>Humidity</h3>
                <div id="hum">--</div>%
            </div>
            <div class="metric">
                <h3>Activity</h3>
                <div id="count">0</div>
            </div>
        </div>
        
        <button onclick="toggleStream()">Toggle Stream</button>
        <a href="/snapshots" class="snapshot-link">View Snapshots</a>
        
        <div class="chart-container">
            <canvas id="chart"></canvas>
        </div>
    </div>
</body>
</html>
"""

class CameraManager:
    def __init__(self):
        self.picam2 = Picamera2()
        self.output = StreamingOutput()
        self.current_mode = None
        
        self.video_config = self.picam2.create_video_configuration(main=STREAM_CONFIG)
        self.still_config = self.picam2.create_still_configuration(main=STILL_CONFIG)
        
        self.switch_to_video()

    def switch_to_video(self):
        with camera_lock:
            if self.current_mode == "video":
                return
            
            try:
                if self.picam2.started:
                    self.picam2.stop_recording()
                self.picam2.configure(self.video_config)
                self.picam2.start_recording(JpegEncoder(), FileOutput(self.output))
                self.current_mode = "video"
            except Exception as e:
                logging.error("Video mode switch failed: %s", e)
                raise

    def switch_to_still(self):
        with camera_lock:
            if self.current_mode == "still":
                return True
            
            try:
                if self.picam2.started:
                    self.picam2.stop_recording()
                self.picam2.configure(self.still_config)
                self.current_mode = "still"
                return True
            except Exception as e:
                logging.error("Still mode switch failed: %s", e)
                return False

    def capture_still(self):
        with camera_lock:
            if self.current_mode != "still":
                return None
            
            try:
                dots.fill((255, 255, 255))
                request = self.picam2.capture_request()
                img = cv2.cvtColor(request.make_array("main"), cv2.COLOR_RGB2BGR)
                request.release()
                return img
            finally:
                dots.fill((0, 0, 0))

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()
        self.red_count = 0
        self.active = True

    def write(self, buf):
        if not self.active or not streaming_enabled:
            return
        
        try:
            img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            
            # Red detection
            lower_red = np.array([0, 120, 70])
            upper_red = np.array([10, 255, 255])
            lower_red2 = np.array([170, 120, 70])
            upper_red2 = np.array([180, 255, 255])
            
            mask = cv2.bitwise_or(
                cv2.inRange(hsv, lower_red, upper_red),
                cv2.inRange(hsv, lower_red2, upper_red2)
            )
            
            self.red_count = 0
            for contour in cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)[0]:
                if cv2.contourArea(contour) > 500:
                    self.red_count += 1
                    x, y, w, h = cv2.boundingRect(contour)
                    cv2.rectangle(img, (x, y), (x+w, y+h), (0, 0, 255), 2)
            
            _, jpeg = cv2.imencode('.jpg', img)
            buf = jpeg.tobytes()
        except Exception as e:
            logging.error("Frame processing error: %s", e)
            return
        
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

    def get_red_count(self):
        return self.red_count

class StreamingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == '/':
                self.send_redirect('/index.html')
            elif self.path == '/index.html':
                self.serve_html()
            elif self.path == '/stream.mjpg':
                self.serve_stream()
            elif self.path == '/sensors':
                self.serve_sensor_data()
            elif self.path == '/count':
                self.serve_red_count()
            elif self.path == '/toggle':
                self.toggle_stream()
            elif self.path == '/snapshots':
                self.serve_snapshots()
            elif self.path.startswith('/snapshot/'):
                self.serve_snapshot_image()
            else:
                self.send_error(404)
        except Exception as e:
            logging.error("Request error: %s", e)
            self.send_error(500)

    def serve_html(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(PAGE.encode())

    def serve_stream(self):
        self.send_response(200)
        self.send_header('Age', '0')
        self.send_header('Cache-Control', 'no-cache, private')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
        self.end_headers()
        
        try:
            while True:
                with camera_manager.output.condition:
                    camera_manager.output.condition.wait()
                    frame = camera_manager.output.frame
                
                self.wfile.write(b'--FRAME\r\n')
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', len(frame))
                self.end_headers()
                self.wfile.write(frame)
                self.wfile.write(b'\r\n')
        except Exception as e:
            logging.warning("Stream closed: %s", e)

    def serve_sensor_data(self):
        with data_lock:
            latest = sensor_data[-1] if sensor_data else {"temperature": 0, "humidity": 0}
            data = {
                "temperature": latest["temperature"],
                "humidity": latest["humidity"],
                "count": camera_manager.output.get_red_count(),
                "history": list(sensor_data)[-100:]
            }
        self.send_json(data)

    def serve_red_count(self):
        count = camera_manager.output.get_red_count()
        self.send_json({"count": count})

    def toggle_stream(self):
        global streaming_enabled
        streaming_enabled = not streaming_enabled
        self.send_json({"success": True})

    def serve_snapshots(self):
        html = """<html><head>
            <title>Snapshots</title>
            <style>
                .day { margin: 20px; padding: 10px; border: 1px solid #ccc; }
                .snapshot { display: inline-block; margin: 10px; text-align: center; }
                img { max-width: 300px; margin: 5px; cursor: pointer; }
                .modal { display: none; position: fixed; z-index: 1; padding-top: 100px; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.9); }
                .modal-content { margin: auto; display: block; max-width: 90%; max-height: 90%; }
                .close { position: absolute; top: 15px; right: 35px; color: #f1f1f1; font-size: 40px; font-weight: bold; cursor: pointer; }
            </style>
            <script>
                function openModal(img) {
                    var modal = document.getElementById('imageModal');
                    var modalImg = document.getElementById('modalImage');
                    modal.style.display = "block";
                    modalImg.src = img.src;
                }
                function closeModal() {
                    document.getElementById('imageModal').style.display = "none";
                }
                window.onclick = function(event) {
                    var modal = document.getElementById('imageModal');
                    if (event.target == modal) {
                        closeModal();
                    }
                }
            </script>
            </head><body><h1>Daily Snapshots</h1>
            <div id="imageModal" class="modal">
                <span class="close" onclick="closeModal()">&times;</span>
                <img class="modal-content" id="modalImage">
            </div>"""
        
        for day in sorted(os.listdir(SNAPSHOT_ROOT), reverse=True):
            day_path = os.path.join(SNAPSHOT_ROOT, day)
            if not os.path.isdir(day_path):
                continue
            
            metadata_path = os.path.join(day_path, 'data.json')
            if not os.path.exists(metadata_path):
                continue
                
            with open(metadata_path) as f:
                records = json.load(f)
            
            html += f'<div class="day"><h2>{day}</h2>'
            for record in records:
                html += f"""
                <div class="snapshot">
                    <img src="/snapshot/{day}/{record["filename"]}" onclick="openModal(this)">
                    <div>{datetime.fromtimestamp(record["timestamp"]).strftime('%H:%M:%S')}</div>
                    <div>Temp: {record["temperature"]:.1f}°F</div>
                    <div>Humidity: {record["humidity"]:.1f}%</div>
                </div>
                """
            html += '</div>'
        
        html += "</body></html>"
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def serve_snapshot_image(self):
        try:
            path_parts = self.path.split('/')[2:]
            if len(path_parts) < 2:
                raise ValueError("Invalid path format")
                
            file_path = os.path.join(SNAPSHOT_ROOT, *path_parts)
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"Snapshot not found: {file_path}")
                
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            with open(file_path, 'rb') as f:
                self.end_headers()
                self.wfile.write(f.read())
        except (ValueError, FileNotFoundError) as e:
            logging.warning(f"Failed to serve snapshot: {str(e)}")
            self.send_error(404)
        except Exception as e:
            logging.error(f"Unexpected error serving snapshot: {str(e)}")
            self.send_error(500)

    def send_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def send_redirect(self, location):
        self.send_response(301)
        self.send_header('Location', location)
        self.end_headers()

def sensor_loop():
    while True:
        try:
            temp = sht.temperature * 9/5 + 32
            hum = sht.relative_humidity
            
            with data_lock:
                sensor_data.append({
                    "time": time.time(),
                    "temperature": temp,
                    "humidity": hum
                })
            
            time.sleep(1)
        except Exception as e:
            logging.error("Sensor error: %s", e)
            time.sleep(5)

def snapshot_loop():
    while True:
        time.sleep(SNAPSHOT_INTERVAL)
        
        try:
            if not camera_manager.switch_to_still():
                continue
                
            img = camera_manager.capture_still()
            if img is None:
                continue
            
            # Add overlay
            with data_lock:
                latest = sensor_data[-1] if sensor_data else {"temperature": 0, "humidity": 0}
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(img, f"Temp: {latest['temperature']:.1f}°F", (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            cv2.putText(img, f"Humidity: {latest['humidity']:.1f}%", (10, 60), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            cv2.putText(img, timestamp, (10, 90), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            
            # Save image
            date_folder = os.path.join(SNAPSHOT_ROOT, datetime.now().strftime("%Y%m%d"))
            os.makedirs(date_folder, exist_ok=True)
            
            filename = f"snapshot_{datetime.now().strftime('%H%M%S')}.jpg"
            cv2.imwrite(os.path.join(date_folder, filename), img)
            
            # Save metadata
            metadata = {
                "timestamp": time.time(),
                "temperature": latest["temperature"],
                "humidity": latest["humidity"],
                "filename": filename
            }
            
            metadata_file = os.path.join(date_folder, "data.json")
            if os.path.exists(metadata_file):
                with open(metadata_file, "r") as f:
                    existing = json.load(f)
                existing.append(metadata)
            else:
                existing = [metadata]
            
            with open(metadata_file, "w") as f:
                json.dump(existing, f)
            
        except Exception as e:
            logging.error("Snapshot error: %s", e)
        finally:
            camera_manager.switch_to_video()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler("hive_monitor.log"),
            logging.StreamHandler()
        ]
    )

    camera_manager = None
    server = None
    
    try:
        camera_manager = CameraManager()
        
        threading.Thread(target=sensor_loop, daemon=True).start()
        threading.Thread(target=snapshot_loop, daemon=True).start()

        class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
            pass

        server = ThreadedHTTPServer(('', PORT), StreamingHandler)
        server.daemon_threads = True
        
        logging.info(f"Server started on port {PORT}")
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down")
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
    finally:
        if camera_manager:
            try:
                camera_manager.picam2.stop_recording()
                camera_manager.picam2.close()
            except Exception as e:
                logging.error(f"Error closing camera: {str(e)}")
        
        if server:
            try:
                server.server_close()
            except Exception as e:
                logging.error(f"Error closing server: {str(e)}")
        
        logging.info("Cleanup complete")
