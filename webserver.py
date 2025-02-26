import io
import logging
import socketserver
import json
import time
import threading
import os
import math
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

# Create snapshots directory if it doesn't exist
SNAPSHOT_ROOT = "snapshots"
os.makedirs(SNAPSHOT_ROOT, exist_ok=True)

# Initialize sensor
sht = adafruit_sht4x.SHT4x(board.I2C())

# Initialize LEDs (4 LEDs)
dots = dotstar.DotStar(board.SCK, board.MOSI, 4, brightness=0.2)
LED_enable = False

# Global flag to control streaming and a timestamp for tracking its duration
streaming_enabled = True
streaming_start_time = time.time()  # streaming starts enabled

# Store sensor readings with thread safety
sensor_data = deque(maxlen=100)
data_lock = Lock()

# Main page HTML with added style for the snapshots link
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
        width: 100%;
        aspect-ratio: 1 / 1;
        background: var(--hive-brown);
        position: relative;
        overflow: hidden;
        border-radius: 16px;
        margin: 1rem auto;
        width: 90%;
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
    /* New style for the snapshots link on the main page */
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
                interaction: {
                    mode: 'nearest',
                    intersect: false
                },
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
                            label: (context) => {
                                return `${context.dataset.label}: ${context.parsed.y.toFixed(1)}`;
                            }
                        }
                    },
                    legend: { 
                        labels: { 
                            color: '#6B4423',
                            font: { size: 14 },
                            boxWidth: 20,
                            padding: 20
                        },
                        position: 'top'
                    },
                    zoom: {
                        pan: { enabled: true, mode: 'x' },
                        zoom: { wheel: { enabled: true }, mode: 'x' }
                    }
                },
                scales: {
                    x: {
                        type: 'time',
                        time: { tooltipFormat: 'HH:mm' },
                        grid: { color: 'rgba(0,0,0,0.05)' },
                        ticks: {
                            color: '#6B4423',
                            font: { size: 12 }
                        }
                    },
                    y: {
                        grid: { color: 'rgba(0,0,0,0.05)' },
                        ticks: { 
                            color: '#6B4423',
                            font: { size: 12 }
                        }
                    }
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

    // Toggle the stream on/off
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
                    <path d="M12 2C14.65 2 17.2 3.05 19.07 4.93C20.95 6.8 22 9.35 22 12C22 17.52 17.52 22 12 22C6.48 22 2 17.52 2 12C2 6.48 6.48 2 12 2M12 4C7.58 4 4 7.58 4 12C4 16.42 7.58 20 12 20C16.42 20 20 16.42 20 12C20 7.58 16.42 4 12 4M12 5C15.87 5 19 8.13 19 12C19 15.87 15.87 19 12 19C8.13 19 5 15.87 5 12C5 8.13 8.13 5 12 5M12 7.5C11.17 7.5 10.5 8.17 10.5 9C10.5 9.83 11.17 10.5 12 10.5C12.83 10.5 13.5 9.83 13.5 9C13.5 8.17 12.83 7.5 12 7.5M8.5 10C7.67 10 7 10.67 7 11.5C7 12.33 7.67 13 8.5 13C9.33 13 10 12.33 10 11.5C10 10.67 9.33 10 8.5 10M15.5 10C14.67 10 14 10.67 14 11.5C14 12.33 14.67 13 15.5 13C16.33 13 17 12.33 17 11.5C17 10.67 16.33 10 15.5 10M12 15C13.66 15 15 13.66 15 12H9C9 13.66 10.34 15 12 15Z"/>
                </svg>
                HiveHealth
            </div>
        </div>
        
        <div class="video-section">
            <div class="status-bar">
                <div class="metric green">
                    <svg class="metric-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12,15A2,2 0 0,1 14,17A2,2 0 0,1 12,19A2,2 0 0,1 10,17A2,2 0 0,1 12,15M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,5A2,2 0 0,1 14,7A2,2 0 0,1 12,9A2,2 0 0,1 10,7A2,2 0 0,1 12,5"/>
                    </svg>
                    <span id="temp">-</span>F
                </div>
                <div class="metric blue">
                    <svg class="metric-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12,3.25C12,3.25 6,10 6,14C6,17.32 8.69,20 12,20A6,6 0 0,0 18,14C18,10 12,3.25 12,3.25M14.47,9.97L15.53,11.03L9.53,17.03L8.47,15.97"/>
                    </svg>
                    <span id="hum">-</span>%
                </div>
                <div class="metric red">
                    <svg class="metric-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M17,15V13H7V15L12,20L17,15M12,4A8,8 0 0,1 20,12A8,8 0 0,1 12,20A8,8 0 0,1 4,12A8,8 0 0,1 12,4"/>
                    </svg>
                    <span id="red-count">0</span>
                </div>
                <!-- Toggle streaming button -->
                <button id="toggleStreamBtn" onclick="toggleStream()">Disable Stream</button>
            </div>
            <img class="video-feed" src="stream.mjpg" />
        </div>
        
        <div class="graph-section">
            <div class="chart-container">
                <canvas id="sensorChart"></canvas>
            </div>
        </div>
        <!-- Styled link to view snapshots -->
        <div style="text-align:center; margin: 1rem;">
            <a href="/snapshots" target="_blank" class="snapshot-link">View Snapshots</a>
        </div>
    </div>
</body>
</html>
"""

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()
        self.red_count = 0

    def write(self, buf):
        # Process frame for red object detection
        img = cv2.imdecode(np.frombuffer(buf, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            # Red color ranges
            lower_red1 = np.array([0, 120, 70])
            upper_red1 = np.array([10, 255, 255])
            lower_red2 = np.array([170, 120, 70])
            upper_red2 = np.array([180, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
            mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
            full_mask = cv2.bitwise_or(mask1, mask2)
            # Find and draw contours
            contours, _ = cv2.findContours(full_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            self.red_count = 0
            for contour in contours:
                if cv2.contourArea(contour) > 500:
                    self.red_count += 1
                    x, y, w, h = cv2.boundingRect(contour)
                    cv2.rectangle(img, (x, y), (x+w, y+h), (0, 0, 255), 2)
            # Encode modified image
            _, jpeg = cv2.imencode('.jpg', img)
            buf = jpeg.tobytes()
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

    def get_red_count(self):
        return self.red_count

class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        global streaming_enabled, streaming_start_time
        if self.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            content = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', '0')
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                    if streaming_enabled:
                        dots.fill((255,255,255))
                    else:
                        dots.fill((0,0,0))
                    if not streaming_enabled:
                        time.sleep(0.1)
                        continue
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.warning('Streaming stopped: %s', str(e))
        elif self.path == '/sensors':
            with data_lock:
                data_copy = list(sensor_data)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data_copy).encode('utf-8'))
        elif self.path == '/count':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'count': output.get_red_count()}).encode('utf-8'))
        elif self.path == '/toggle':
            streaming_enabled = not streaming_enabled
            if streaming_enabled:
                streaming_start_time = time.time()
            else:
                streaming_start_time = None
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'streaming': streaming_enabled}).encode('utf-8'))
        elif self.path == '/snapshots':
            # Build a page listing each day's folder with averages and a centered layout.
            html_content = """<html>
<head>
  <title>Daily Snapshots</title>
  <style>
    body { font-family: 'Roboto Condensed', sans-serif; margin: 20px; background: #f9f9f9; }
    h1 { text-align: center; }
    .day-container { background: #fff; margin-bottom: 30px; padding: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
    .stats { margin-bottom: 10px; }
    .snapshots { display: flex; flex-wrap: wrap; justify-content: center; }
    .snapshot { margin: 10px; border: 3px solid #ccc; cursor: pointer; }
    .snapshot.outlier { border-color: #e74c3c; }
    .snapshot img { display: block; max-width: 200px; }
    .snapshot p { margin: 5px; font-size: 0.9em; text-align: center; }
    /* Modal styles */
    .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; overflow: auto; background-color: rgba(0,0,0,0.9); }
    .modal-content { margin: auto; display: block; max-width: 90%; max-height: 90%; }
    .modal-close { position: absolute; top: 20px; right: 35px; color: #f1f1f1; font-size: 40px; font-weight: bold; cursor: pointer; }
  </style>
</head>
<body>
  <h1>Daily Snapshots</h1>
  <div id="days-container">
"""
            for day in sorted(os.listdir(SNAPSHOT_ROOT), reverse=True):
                day_folder = os.path.join(SNAPSHOT_ROOT, day)
                if not os.path.isdir(day_folder):
                    continue
                metadata_file = os.path.join(day_folder, "data.json")
                if not os.path.exists(metadata_file):
                    continue
                try:
                    with open(metadata_file, "r") as f:
                        records = json.load(f)
                except Exception:
                    records = []
                if not records:
                    continue
                temps = [r["temperature"] for r in records]
                hums = [r["humidity"] for r in records]
                reds = [r["red_count"] for r in records]
                avg_temp = sum(temps)/len(temps)
                avg_hum = sum(hums)/len(hums)
                avg_red = sum(reds)/len(reds)
                std_temp = math.sqrt(sum((t - avg_temp)**2 for t in temps)/len(temps))
                std_hum = math.sqrt(sum((h - avg_hum)**2 for h in hums)/len(hums))
                std_red = math.sqrt(sum((r - avg_red)**2 for r in reds)/len(reds))
                html_content += f"""<div class="day-container">
  <h2>{day}</h2>
  <div class="stats">
    <strong>Averages:</strong> Temp: {avg_temp:.1f} F, Humidity: {avg_hum:.1f}%, Red Dot Count: {avg_red:.1f}
  </div>
  <div class="snapshots">"""
                for rec in sorted(records, key=lambda x: x["timestamp"], reverse=True):
                    outlier = (
                        rec["temperature"] > avg_temp + 2*std_temp or
                        rec["humidity"] > avg_hum + 2*std_hum or
                        rec["red_count"] > avg_red + 2*std_red
                    )
                    image_url = f"/snapshot/{day}/{rec['filename']}"
                    html_content += f"""<div class="snapshot{' outlier' if outlier else ''}" onclick="openModal('{image_url}')">
  <img src="{image_url}">
  <p>{time.strftime('%H:%M:%S', time.localtime(rec['timestamp']))}<br>
     Temp: {rec['temperature']:.1f} F, Hum: {rec['humidity']:.1f}%, Red: {rec['red_count']}</p>
</div>"""
                html_content += "</div></div>"
            html_content += """
  </div>
  <!-- Modal for full screen image -->
  <div id="myModal" class="modal" onclick="closeModal()">
    <span class="modal-close" onclick="closeModal()">&times;</span>
    <img class="modal-content" id="modalImage">
  </div>
  <script>
    function openModal(src) {
      var modal = document.getElementById("myModal");
      var modalImg = document.getElementById("modalImage");
      modal.style.display = "block";
      modalImg.src = src;
    }
    function closeModal() {
      document.getElementById("myModal").style.display = "none";
    }
  </script>
</body>
</html>
"""
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
        elif self.path.startswith('/snapshot/'):
            parts = self.path.split('/')
            if len(parts) >= 4:
                day = parts[2]
                filename = "/".join(parts[3:])
                file_path = os.path.join(SNAPSHOT_ROOT, day, filename)
                if os.path.exists(file_path):
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/jpeg')
                    self.end_headers()
                    with open(file_path, 'rb') as f:
                        self.wfile.write(f.read())
                    return
            self.send_error(404)
            self.end_headers()
        else:
            self.send_error(404)
            self.end_headers()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def sensor_loop():
    while True:
        try:
            temp = round(sht.temperature, 3)
            temp = temp * 9/5 + 32  # Fahrenheit conversion
            hum = round(sht.relative_humidity, 3)
            with data_lock:
                if (not sensor_data or 
                    time.time() - sensor_data[-1]['time'] >= 10 or
                    abs(temp - sensor_data[-1]['temperature']) > 0.1 or
                    abs(hum - sensor_data[-1]['humidity']) > 0.5):
                    sensor_data.append({
                        "time": time.time(),
                        "temperature": temp,
                        "humidity": hum
                    })
        except Exception as e:
            logging.error("Sensor error: %s", e)
        time.sleep(1)

def streaming_timeout_monitor():
    global streaming_enabled, streaming_start_time
    while True:
        if streaming_enabled and streaming_start_time is not None:
            if time.time() - streaming_start_time > 60:
                logging.info("Streaming has been enabled for over 60 seconds. Disabling streaming.")
                streaming_enabled = False
                streaming_start_time = None
        time.sleep(1)

def snapshot_loop():
    global output
    while True:
        time.sleep(60)  # Wait one minute
        dots.fill((255, 255, 255))
        time.sleep(0.1)
        with output.condition:
            frame_data = output.frame
        dots.fill((0, 0, 0))
        if frame_data is None:
            continue
        img = cv2.imdecode(np.frombuffer(frame_data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        with data_lock:
            if sensor_data:
                latest_sensor = sensor_data[-1]
            else:
                latest_sensor = {"temperature": 0, "humidity": 0, "time": time.time()}
        red_count = output.get_red_count()
        overlay_text = f"Temp: {latest_sensor['temperature']:.1f} F, Hum: {latest_sensor['humidity']:.1f}%, Red Dots: {red_count}"
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        cv2.putText(img, overlay_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,255), 2)
        cv2.putText(img, timestamp, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,255), 2)
        day_folder_name = time.strftime("%Y%m%d")
        day_folder = os.path.join(SNAPSHOT_ROOT, day_folder_name)
        os.makedirs(day_folder, exist_ok=True)
        filename = f"snapshot_{time.strftime('%H%M%S')}.jpg"
        file_path = os.path.join(day_folder, filename)
        cv2.imwrite(file_path, img)
        metadata_file = os.path.join(day_folder, "data.json")
        record = {
            "timestamp": time.time(),
            "temperature": latest_sensor["temperature"],
            "humidity": latest_sensor["humidity"],
            "red_count": red_count,
            "filename": filename
        }
        if os.path.exists(metadata_file):
            try:
                with open(metadata_file, "r") as f:
                    records = json.load(f)
            except Exception:
                records = []
        else:
            records = []
        records.append(record)
        with open(metadata_file, "w") as f:
            json.dump(records, f)

# Initialize camera with square aspect ratio
picam2 = Picamera2()
config = picam2.create_video_configuration({
    'size': (640, 640),
    'format': 'XRGB8888'
})
picam2.configure(config)
output = StreamingOutput()
picam2.start_recording(JpegEncoder(), FileOutput(output))

sensor_thread = threading.Thread(target=sensor_loop, daemon=True)
sensor_thread.start()

timeout_thread = threading.Thread(target=streaming_timeout_monitor, daemon=True)
timeout_thread.start()

snapshot_thread = threading.Thread(target=snapshot_loop, daemon=True)
snapshot_thread.start()

try:
    address = ('', 7123)
    server = StreamingServer(address, StreamingHandler)
    server.serve_forever()
finally:
    picam2.stop_recording()
