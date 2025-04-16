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

PAGE = """\
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
    
    html, body {
        margin: 0;
        padding: 0;
        height: 100%;
        font-family: 'Roboto Condensed', sans-serif;
        background: linear-gradient(45deg, #fff5e6, #fff);
        overflow: hidden;
    }
    
    .container {
        display: flex;
        flex-direction: column;
        height: 100vh;
        background-image: url("data:image/svg+xml,%3Csvg width='100' height='100' viewBox='0 0 100 100' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M50 0L100 50L50 100L0 50L50 0' fill='%23FFB347' fill-opacity='0.05'/%3E%3C/svg%3E");
    }
    
    .header {
        background: var(--hive-brown);
        padding: 1rem 2rem;
        display: flex;
        align-items: center;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
    
    .logo {
        font-family: 'Honeybee', cursive;
        font-size: 2.5rem;
        color: var(--comb-yellow);
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    
    .video-section {
        width: 90%;
        aspect-ratio: 1 / 1;
        background: var(--hive-brown);
        position: relative;
        overflow: hidden;
        border-radius: 16px;
        margin: 1rem auto;
        box-shadow: 0 8px 24px rgba(0,0,0,0.15);
    }
    
    .status-bar {
        position: absolute;
        bottom: 1.5rem;
        left: 50%;
        transform: translateX(-50%);
        display: flex;
        gap: 1.5rem;
        z-index: 1;
        background: rgba(255, 255, 255, 0.9);
        padding: 1rem 2rem;
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
    
    .metric {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 1.1rem;
        color: var(--hive-brown);
    }
    
    .metric-icon {
        width: 24px;
        height: 24px;
    }
    
    .metric.green { color: var(--healthy-green); }
    .metric.red { color: var(--alert-red); }
    .metric.blue { color: #3498db; }
    
    .graph-section {
        height: 40vh;
        padding: 0 1.5rem 1.5rem;
    }
    
    .chart-container {
        height: 100%;
        background: white;
        border-radius: 16px;
        padding: 1.5rem;
        box-shadow: 0 8px 24px rgba(0,0,0,0.1);
    }
    
    .video-feed {
        position: absolute;
        height: 100%;
        width: auto;
        left: 50%;
        transform: translateX(-50%);
        border-radius: 8px;
    }
    
    #sensorChart {
        width: 100% !important;
        height: 100% !important;
    }
    
    /* Styled snapshots link */
    .snapshot-link {
         display: inline-block;
         padding: 10px 20px;
         background: var(--comb-yellow);
         color: #fff;
         text-decoration: none;
         border-radius: 5px;
         font-size: 1.2em;
         transition: background 0.3s ease;
         box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .snapshot-link:hover {
         background: #f39c12;
    }
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
<script>
    let sensorChart;
    function updateMetrics() {
        fetch('/sensors')
        .then(r => r.json())
        .then(data => {
            if(data.length > 0) {
                const latest = data[data.length - 1];
                document.getElementById('temp').textContent = latest.temperature.toFixed(1);
                document.getElementById('hum').textContent = latest.humidity.toFixed(1);
            }
        });
        fetch('/count')
        .then(r => r.json())
        .then(data => {
            document.getElementById('red-count').textContent = data.count;
        });
    }
    function initChart() {
        const ctx = document.getElementById('sensorChart').getContext('2d');
        const gradientTemp = ctx.createLinearGradient(0, 0, 0, 400);
        gradientTemp.addColorStop(0, 'rgba(255, 179, 71, 0.4)');
        gradientTemp.addColorStop(1, 'rgba(255, 179, 71, 0)');
        const gradientHum = ctx.createLinearGradient(0, 0, 0, 400);
        gradientHum.addColorStop(0, 'rgba(52, 152, 219, 0.4)');
        gradientHum.addColorStop(1, 'rgba(52, 152, 219, 0)');
        sensorChart = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: [{
                    label: 'Temperature (F)',
                    borderColor: '#FFB347',
                    backgroundColor: gradientTemp,
                    tension: 0.3,
                    pointRadius: 4,
                    borderWidth: 2,
                    fill: true
                },{
                    label: 'Humidity (%)',
                    borderColor: '#3498db',
                    backgroundColor: gradientHum,
                    tension: 0.3,
                    pointRadius: 4,
                    borderWidth: 2,
                    fill: true
                }]
            },
            options: {
                maintainAspectRatio: false,
                interaction: { mode: 'nearest', intersect: false },
                plugins: {
                    tooltip: {
                        backgroundColor: 'rgba(107, 68, 35, 0.95)',
                        titleColor: '#FFB347',
                        bodyColor: '#fff',
                        borderColor: '#FFB347',
                        borderWidth: 1,
                        padding: 12,
                        callbacks: {
                            title: (context) => {
                                const date = new Date(context[0].parsed.x);
                                return date.toLocaleTimeString();
                            },
                            label: (context) => `${context.dataset.label}: ${context.parsed.y.toFixed(1)}`
                        }
                    },
                    legend: {
                        labels: { color: '#6B4423', font: { size: 14 }, boxWidth: 20, padding: 20 },
                        position: 'top'
                    },
                    zoom: { pan: { enabled: true, mode: 'x' }, zoom: { wheel: { enabled: true }, mode: 'x' } }
                },
                scales: {
                    x: { type: 'time', time: { tooltipFormat: 'HH:mm' }, grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { color: '#6B4423', font: { size: 12 } } },
                    y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { color: '#6B4423', font: { size: 12 } } }
                }
            }
        });
    }
    function updateChart() {
        fetch('/sensors')
        .then(r => r.json())
        .then(data => {
            sensorChart.data.datasets[0].data = data.map(d => ({x: d.time*1000, y: d.temperature}));
            sensorChart.data.datasets[1].data = data.map(d => ({x: d.time*1000, y: d.humidity}));
            sensorChart.update();
        });
    }
    function toggleStream() {
        fetch('/toggle')
        .then(response => response.json())
        .then(data => {
            const btn = document.getElementById('toggleStreamBtn');
            btn.textContent = data.streaming ? "Disable Stream" : "Enable Stream";
        });
    }
    window.addEventListener('load', () => {
        initChart();
        setInterval(updateMetrics, 1000);
        setInterval(updateChart, 10000);
    });
</script>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">
                <svg class="metric-icon" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 2C14.65 2 17.2 3.05 19.07 4.93C20.95 6.8 22 9.35 22 12C22 17.52 17.52 22 12 22C6.48 22 2 17.52 2 12C2 6.48 6.48 2 12 2"/>
                </svg>
                HiveHealth
            </div>
        </div>
        <div class="video-section">
            <div class="status-bar">
                <div class="metric green">
                    <svg class="metric-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12,15A2,2 0 0,1 14,17A2,2 0 0,1 12,19A2,2 0 0,1 10,17"/>
                    </svg>
                    <span id="temp">-</span>F
                </div>
                <div class="metric blue">
                    <svg class="metric-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12,3.25C12,3.25 6,10 6,14"/>
                    </svg>
                    <span id="hum">-</span>%
                </div>
                <div class="metric red">
                    <svg class="metric-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12,2A10,10 0 0,0 2,12"/>
                    </svg>
                    <span id="red-count">0</span>
                </div>
                <button id="toggleStreamBtn" onclick="toggleStream()">Disable Stream</button>
            </div>
            <img class="video-feed" src="stream.mjpg" />
        </div>
        <div class="graph-section">
            <div class="chart-container">
                <canvas id="sensorChart"></canvas>
            </div>
        </div>
        <div style="text-align:center; margin: 1rem;">
            <a href="/snapshots" target="_blank" class="snapshot-link">View Snapshots</a>
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
        
        # Initialize both configurations
        self.video_config = self.picam2.create_video_configuration(main=STREAM_CONFIG)
        self.still_config = self.picam2.create_still_configuration(main=STILL_CONFIG)
        
        # Start with video mode
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
                logging.info("Successfully switched to video mode")
            except Exception as e:
                logging.error(f"Video mode switch failed: {str(e)}")
                raise

    def switch_to_still(self):
        with camera_lock:
            if self.current_mode == "still":
                return True
            
            try:
                if self.picam2.started:
                    self.picam2.stop_recording()
                    time.sleep(0.5)  # Give time for camera to settle
                
                # Configure for still capture
                self.picam2.configure(self.still_config)
                time.sleep(0.5)  # Give time for camera to adjust to new settings
                
                # Start the camera in still mode
                self.picam2.start()
                time.sleep(0.5)  # Give time for camera to start
                
                self.current_mode = "still"
                logging.info("Successfully switched to still mode")
                return True
            except Exception as e:
                logging.error(f"Still mode switch failed: {str(e)}")
                return False

    def capture_still(self):
        with camera_lock:
            if self.current_mode != "still":
                logging.error("Camera not in still mode")
                return None
            
            try:
                # Turn on LEDs for the capture
                dots.fill((255, 255, 255))
                time.sleep(0.1)  # Small delay to ensure LEDs are on
                
                # Capture the image
                logging.info("Attempting to capture still image")
                
                if not self.picam2.started:
                    logging.error("Camera not started in still mode")
                    return None
                
                try:
                    # Create capture request
                    request = self.picam2.capture_request()
                    logging.info("Capture request created")
                    
                    # Get the image data
                    array = request.make_array("main")
                    logging.info(f"Image array created with shape: {array.shape}")
                    
                    # Convert to BGR
                    img = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
                    logging.info("Image converted to BGR")
                    
                    # Release the request
                    request.release()
                    logging.info("Capture request released")
                    
                    # Turn off LEDs
                    dots.fill((0, 0, 0))
                    
                    return img
                except Exception as e:
                    logging.error(f"Error during capture process: {str(e)}")
                    if 'request' in locals():
                        try:
                            request.release()
                        except:
                            pass
                    return None
                
            except Exception as e:
                logging.error(f"Error in capture_still: {str(e)}")
                dots.fill((0, 0, 0))  # Ensure LEDs are turned off even if capture fails
                return None
            finally:
                # Stop the camera after capture
                try:
                    if self.picam2.started:
                        self.picam2.stop()
                        logging.info("Camera stopped after capture")
                except Exception as e:
                    logging.error(f"Error stopping camera after capture: {str(e)}")

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
            
            # Flip the image horizontally and rotate 180 degrees
            img = cv2.flip(img, 1)  # Horizontal flip
            img = cv2.rotate(img, cv2.ROTATE_180)  # 180 degree rotation
            
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
        if streaming_enabled:
            dots.fill((255, 255, 255))  # Turn LEDs on
            camera_manager.switch_to_video()  # Ensure camera is in video mode
        else:
            dots.fill((0, 0, 0))  # Turn LEDs off
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
            logging.info("Starting snapshot process")
            
            if not camera_manager.switch_to_still():
                logging.error("Failed to switch to still mode")
                continue
            
            logging.info("Camera switched to still mode, attempting capture")
            img = camera_manager.capture_still()
            if img is None:
                logging.error("Failed to capture still image")
                continue
            
            logging.info("Image captured successfully, processing...")
            
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
            filepath = os.path.join(date_folder, filename)
            
            logging.info(f"Attempting to save image to {filepath}")
            success = cv2.imwrite(filepath, img)
            if not success:
                logging.error(f"Failed to save image to {filepath}")
                continue
            
            logging.info("Image saved successfully, updating metadata")
            
            # Save metadata
            metadata = {
                "timestamp": time.time(),
                "temperature": latest["temperature"],
                "humidity": latest["humidity"],
                "filename": filename
            }
            
            metadata_file = os.path.join(date_folder, "data.json")
            try:
                if os.path.exists(metadata_file):
                    with open(metadata_file, "r") as f:
                        existing = json.load(f)
                    existing.append(metadata)
                else:
                    existing = [metadata]
                
                with open(metadata_file, "w") as f:
                    json.dump(existing, f)
                
                logging.info(f"Successfully saved snapshot: {filename}")
            except Exception as e:
                logging.error(f"Error saving metadata: {str(e)}")
            
        except Exception as e:
            logging.error(f"Snapshot error: {str(e)}")
        finally:
            if streaming_enabled:
                logging.info("Switching back to video mode")
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
