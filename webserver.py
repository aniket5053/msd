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

# Main page HTML with theme styling and a nicely styled snapshots link
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

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()
        self.red_count = 0

    def write(self, buf):
        img = cv2.imdecode(np.frombuffer(buf, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            lower_red1 = np.array([0, 120, 70])
            upper_red1 = np.array([10, 255, 255])
            lower_red2 = np.array([170, 120, 70])
            upper_red2 = np.array([180, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
            mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
            full_mask = cv2.bitwise_or(mask1, mask2)
            contours, _ = cv2.findContours(full_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            self.red_count = 0
            for contour in contours:
                if cv2.contourArea(contour) > 500:
                    self.red_count += 1
                    x, y, w, h = cv2.boundingRect(contour)
                    cv2.rectangle(img, (x, y), (x+w, y+h), (0, 0, 255), 2)
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
            html_content = """<html>
<head>
  <title>Daily Snapshots</title>
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
      font-family: 'Roboto Condensed', sans-serif;
      background: linear-gradient(45deg, #fff5e6, #fff);
    }
    .container {
      max-width: 1200px;
      margin: auto;
      padding: 20px;
    }
    .header {
      background: var(--hive-brown);
      padding: 1rem 2rem;
      color: var(--comb-yellow);
      text-align: center;
      font-family: 'Honeybee', cursive;
      font-size: 2.5em;
      margin-bottom: 20px;
      border-radius: 5px;
    }
    details {
      border: 2px solid var(--hive-brown);
      border-radius: 8px;
      margin-bottom: 15px;
      background: rgba(255, 255, 255, 0.9);
      box-shadow: 0 2px 6px rgba(0,0,0,0.1);
    }
    details[open] {
      background: rgba(255, 179, 71, 0.05);
    }
    summary {
      padding: 15px 20px;
      background: var(--hive-brown);
      color: var(--comb-yellow);
      font-family: 'Honeybee', cursive;
      font-size: 1.4em;
      cursor: pointer;
      list-style: none;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    summary::-webkit-details-marker {
      display: none;
    }
    summary:after {
      content: '▶';
      transition: transform 0.2s;
      color: var(--comb-yellow);
      margin-left: 15px;
    }
    details[open] summary:after {
      transform: rotate(90deg);
    }
    .day-stats {
      font-family: 'Roboto Condensed', sans-serif;
      font-size: 0.8em;
      color: var(--comb-yellow);
      margin-left: 20px;
    }
    .snapshots {
      padding: 20px;
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: 15px;
    }
    .snapshot {
      margin: 10px;
      border: 3px solid #ccc;
      cursor: pointer;
      display: flex;
      flex-direction: column;
      align-items: center;
      border-radius: 5px;
      transition: transform 0.2s;
    }
    .snapshot:hover {
      transform: translateY(-3px);
    }
    .snapshot.outlier {
      border-color: var(--alert-red);
    }
    .snapshot img {
      display: block;
      max-width: 200px;
      border-radius: 5px;
    }
    .snapshot p {
      margin: 5px;
      font-size: 0.9em;
      text-align: center;
      color: var(--hive-brown);
    }
    .modal {
      display: none;
      position: fixed;
      z-index: 1000;
      left: 0;
      top: 0;
      width: 100%;
      height: 100%;
      background-color: rgba(0,0,0,0.9);
    }
    .modal-content {
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      max-width: 90%;
      max-height: 90%;
      border-radius: 10px;
    }
    .modal-close {
      position: absolute;
      top: 20px;
      right: 35px;
      color: #f1f1f1;
      font-size: 40px;
      font-weight: bold;
      cursor: pointer;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">Daily Snapshots</div>"""

            # Loop over day folders
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
                
                try:
                    human_date = datetime.strptime(day, "%Y%m%d").strftime("%d %B, %Y")
                except Exception:
                    human_date = day
                
                temps = [r["temperature"] for r in records]
                hums = [r["humidity"] for r in records]
                reds = [r["red_count"] for r in records]
                avg_temp = sum(temps)/len(temps)
                avg_hum = sum(hums)/len(hums)
                avg_red = sum(reds)/len(reds)
                
                html_content += f"""<details class="day-container">
                  <summary>
                    {human_date}
                    <span class="day-stats">
                      Avg: {avg_temp:.1f}°F | {avg_hum:.1f}% Hum | {avg_red:.1f} Red Dots
                    </span>
                  </summary>
                  <div class="snapshots">"""
                
                std_temp = math.sqrt(sum((t - avg_temp)**2 for t in temps)/len(temps))
                std_hum = math.sqrt(sum((h - avg_hum)**2 for h in hums)/len(hums))
                std_red = math.sqrt(sum((r - avg_red)**2 for r in reds)/len(reds))
                
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
                         Temp: {rec['temperature']:.1f}F, Hum: {rec['humidity']:.1f}%, Red: {rec['red_count']}</p>
                    </div>"""
                
                html_content += "</div></details>"

            html_content += """
  </div>
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
</html>"""
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
        time.sleep(0.5)  # LED flash duration (changed from 0.1 to 0.5 seconds)
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