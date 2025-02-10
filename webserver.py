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
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

# Initialize sensor
sht = adafruit_sht4x.SHT4x(board.I2C())

# Store sensor readings with thread safety
sensor_data = deque(maxlen=100)
data_lock = Lock()

PAGE = """\
<html>
<head>
<title>BeeCam Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
    :root {
        --primary: #2c3e50;
        --secondary: #3498db;
        --success: #27ae60;
        --danger: #e74c3c;
        --background: #f8f9fa;
        --card-bg: #ffffff;
    }

    html, body {
        margin: 0;
        padding: 0;
        height: 100%;
        font-family: 'Inter', sans-serif;
        background-color: var(--background);
    }

    .dashboard {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 24px;
        height: 100vh;
        padding: 24px;
        box-sizing: border-box;
    }

    .card {
        background: var(--card-bg);
        border-radius: 16px;
        padding: 24px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        border: 1px solid rgba(0,0,0,0.08);
    }

    .video-card {
        grid-column: 1 / 2;
        display: flex;
        flex-direction: column;
        overflow: hidden;
    }

    .metrics-card {
        grid-column: 2 / 3;
        display: grid;
        grid-template-rows: auto 1fr;
        gap: 24px;
    }

    .video-container {
        flex: 1;
        background: #000;
        border-radius: 12px;
        overflow: hidden;
        position: relative;
    }

    .video-feed {
        width: 100%;
        height: 100%;
        object-fit: contain;
    }

    .status-bar {
        display: flex;
        gap: 16px;
        margin-bottom: 24px;
    }

    .metric {
        padding: 16px 24px;
        border-radius: 12px;
        background: rgba(45, 156, 219, 0.1);
        display: flex;
        align-items: center;
        gap: 12px;
    }

    .metric-icon {
        width: 24px;
        height: 24px;
    }

    .metric.red {
        background: rgba(231, 76, 60, 0.1);
        color: var(--danger);
    }

    .metric.green {
        background: rgba(39, 174, 96, 0.1);
        color: var(--success);
    }

    .metric.blue {
        background: rgba(52, 152, 219, 0.1);
        color: var(--secondary);
    }

    .metric-value {
        font-weight: 600;
        font-size: 1.2em;
    }

    .chart-container {
        height: 100%;
        min-height: 300px;
    }

    h1 {
        margin: 0 0 24px 0;
        color: var(--primary);
        font-weight: 600;
        font-size: 1.5em;
    }
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
<script>
    let sensorChart;

    function initChart() {
        const ctx = document.getElementById('sensorChart').getContext('2d');
        sensorChart = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: [{
                    label: 'Temperature (°C)',
                    borderColor: '#e74c3c',
                    backgroundColor: 'rgba(231, 76, 60, 0.1)',
                    tension: 0.3,
                    borderWidth: 2,
                    pointRadius: 3
                },{
                    label: 'Humidity (%)',
                    borderColor: '#3498db',
                    backgroundColor: 'rgba(52, 152, 219, 0.1)',
                    tension: 0.3,
                    borderWidth: 2,
                    pointRadius: 3
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
                        backgroundColor: 'rgba(44, 62, 80, 0.95)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: 'rgba(255,255,255,0.1)',
                        borderWidth: 1,
                        padding: 12,
                        callbacks: {
                            title: (context) => new Date(context[0].parsed.x).toLocaleTimeString(),
                            label: (context) => `${context.dataset.label}: ${context.parsed.y.toFixed(1)}`
                        }
                    },
                    legend: {
                        labels: {
                            color: var(--primary),
                            boxWidth: 12,
                            padding: 16
                        }
                    }
                },
                scales: {
                    x: {
                        type: 'time',
                        grid: { color: 'rgba(0,0,0,0.05)' },
                        ticks: {
                            color: 'rgba(0,0,0,0.6)',
                            maxRotation: 0,
                            autoSkip: true
                        }
                    },
                    y: {
                        grid: { color: 'rgba(0,0,0,0.05)' },
                        ticks: { color: 'rgba(0,0,0,0.6)' }
                    }
                }
            }
        });
    }

    function updateData() {
        fetch('/sensors')
            .then(r => r.json())
            .then(data => {
                document.getElementById('temp').textContent = data.length ? data[data.length-1].temperature.toFixed(1) : '-';
                document.getElementById('hum').textContent = data.length ? data[data.length-1].humidity.toFixed(1) : '-';
                
                sensorChart.data.datasets[0].data = data.map(d => ({x: d.time*1000, y: d.temperature}));
                sensorChart.data.datasets[1].data = data.map(d => ({x: d.time*1000, y: d.humidity}));
                sensorChart.update();
            });

        fetch('/count')
            .then(r => r.json())
            .then(data => document.getElementById('red-count').textContent = data.count);
    }

    window.addEventListener('load', () => {
        initChart();
        setInterval(updateData, 10000);
        setInterval(() => fetch('/count').then(r => r.json()).then(data => {
            document.getElementById('red-count').textContent = data.count
        }), 1000);
    });
</script>
</head>
<body>
    <div class="dashboard">
        <div class="card video-card">
            <h1>Live Camera Feed</h1>
            <div class="status-bar">
                <div class="metric green">
                    <span class="metric-value" id="temp">-</span>
                    <span>°C</span>
                </div>
                <div class="metric blue">
                    <span class="metric-value" id="hum">-</span>
                    <span>% RH</span>
                </div>
                <div class="metric red">
                    <span class="metric-value" id="red-count">0</span>
                    <span>Objects</span>
                </div>
            </div>
            <div class="video-container">
                <img class="video-feed" src="stream.mjpg" />
            </div>
        </div>
        
        <div class="card metrics-card">
            <h1>Environmental Trends</h1>
            <div class="chart-container">
                <canvas id="sensorChart"></canvas>
            </div>
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
            
            # Red color detection
            lower_red = np.array([0, 120, 70])
            upper_red = np.array([10, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red, upper_red)
            
            lower_red = np.array([170, 120, 70])
            upper_red = np.array([180, 255, 255])
            mask2 = cv2.inRange(hsv, lower_red, upper_red)
            
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

# Initialize camera
picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
output = StreamingOutput()
picam2.start_recording(JpegEncoder(), FileOutput(output))

# Start sensor thread
sensor_thread = threading.Thread(target=sensor_loop, daemon=True)
sensor_thread.start()

try:
    address = ('', 7123)
    server = StreamingServer(address, StreamingHandler)
    server.serve_forever()
finally:
    picam2.stop_recording()