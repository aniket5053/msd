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

# Store last 100 sensor readings with thread safety
sensor_data = deque(maxlen=100)
data_lock = Lock()

PAGE = """\
<html>
<head>
<title>BeeCam - Environmental Monitoring</title>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap" rel="stylesheet">
<style>
    html, body {
        margin: 0;
        padding: 0;
        height: 100%;
        overflow: hidden;
        font-family: 'Roboto', sans-serif;
        background-color: #1a1a1a;
    }
    .container {
        display: flex;
        flex-direction: column;
        height: 100vh;
    }
    .dashboard {
        display: grid;
        grid-template-columns: 1fr 1fr;
        height: calc(100vh - 80px);
        gap: 10px;
        padding: 10px;
    }
    h1 {
        color: #ffffff;
        margin: 15px;
        font-size: 2em;
    }
    .status-bar {
        display: flex;
        gap: 15px;
        padding: 0 15px;
    }
    .metric {
        background: rgba(255, 255, 255, 0.1);
        padding: 10px 20px;
        border-radius: 8px;
        color: white;
        font-size: 1.1em;
    }
    .metric.red { color: #ff4444; }
    .metric.green { color: #44ff44; }
    .metric.blue { color: #4444ff; }
    #sensorChart {
        width: 100% !important;
        height: 100% !important;
        background: #2a2a2a;
        border-radius: 8px;
    }
    .video-container {
        background: #000;
        border-radius: 8px;
        overflow: hidden;
        height: 100%;
    }
    .video-feed {
        width: 100%;
        height: 100%;
        object-fit: cover;
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
        sensorChart = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: [{
                    label: 'Temperature (°C)',
                    borderColor: '#ff4444',
                    backgroundColor: '#ff444433',
                    tension: 0.2
                },{
                    label: 'Humidity (%)',
                    borderColor: '#4444ff',
                    backgroundColor: '#4444ff33',
                    tension: 0.2
                }]
            },
            options: {
                maintainAspectRatio: false,
                scales: {
                    x: {
                        type: 'time',
                        grid: { color: '#404040' },
                        ticks: { color: '#fff' }
                    },
                    y: {
                        grid: { color: '#404040' },
                        ticks: { color: '#fff' }
                    }
                },
                plugins: {
                    legend: { labels: { color: '#fff' } }
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

    window.addEventListener('load', () => {
        initChart();
        setInterval(updateMetrics, 1000);
        setInterval(updateChart, 1000);
    });
</script>
</head>
<body>
    <div class="container">
        <h1>BeeCam Environmental Monitor</h1>
        <div class="status-bar">
            <div class="metric green">Temp: <span id="temp">-</span>°C</div>
            <div class="metric blue">Humidity: <span id="hum">-</span>%</div>
            <div class="metric red">Objects: <span id="red-count">0</span></div>
        </div>
        <div class="dashboard">
            <div class="video-container">
                <img class="video-feed" src="stream.mjpg" />
            </div>
            <canvas id="sensorChart"></canvas>
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
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

    def set_red_count(self, count):
        self.red_count = count

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
            self.wfile.write(json.dumps({
                'count': output.get_red_count()
            }).encode('utf-8'))
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