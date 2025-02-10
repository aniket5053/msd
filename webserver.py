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
    body {
        font-family: 'Roboto', sans-serif;
        background-color: #f8f9fa;
        margin: 0;
        padding: 15px;
    }
    .container {
        max-width: 1600px;
        margin: 0 auto;
    }
    .dashboard {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 40px;
        margin-top: 30px;
    }
    .card {
        background: white;
        border-radius: 15px;
        box-shadow: 0 6px 10px rgba(0, 0, 0, 0.1);
        padding: 30px;
        min-height: 600px;
    }
    h1 {
        color: #2c3e50;
        font-weight: 700;
        margin-bottom: 0;
        font-size: 2.5em;
        margin-bottom: 25px;
    }
    .status-bar {
        display: flex;
        justify-content: space-between;
        margin-bottom: 35px;
        gap: 25px;
    }
    .metric {
        background: #e9ecef;
        padding: 20px 35px;
        border-radius: 12px;
        font-weight: 700;
        font-size: 1.2em;
        flex-grow: 1;
        text-align: center;
    }
    .metric.red {
        background: #fff5f5;
        color: #e53e3e;
    }
    canvas {
        width: 100% !important;
        height: 550px !important;
    }
    .video-container {
        border-radius: 15px;
        overflow: hidden;
        height: 100%;
    }
    .video-container img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
<script type="text/javascript">
    let sensorChart;

    function updateMetrics() {
        fetch('/sensors')
        .then(response => response.json())
        .then(data => {
            if(data.length > 0) {
                const latest = data[data.length - 1];
                document.getElementById('temperature').innerHTML = 
                    `${latest.temperature.toFixed(1)}&deg;C`;
                document.getElementById('humidity').innerHTML = 
                    `${latest.humidity.toFixed(1)}%`;
            }
        });

        fetch('/count')
        .then(response => response.json())
        .then(data => {
            document.getElementById('red-count').innerText = data.count;
        });
    }

    function initChart() {
        const ctx = document.getElementById('sensorChart').getContext('2d');
        sensorChart = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: [
                    {
                        label: 'Temperature (°C)',
                        borderColor: '#e53e3e',
                        backgroundColor: '#e53e3e22',
                        tension: 0.3,
                        pointRadius: 2,
                        fill: true
                    },
                    {
                        label: 'Humidity (%)',
                        borderColor: '#3182ce',
                        backgroundColor: '#3182ce22',
                        tension: 0.3,
                        pointRadius: 2,
                        fill: true
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        type: 'time',
                        time: {
                            unit: 'minute',
                            tooltipFormat: 'HH:mm:ss'
                        },
                        grid: { display: false }
                    },
                    y: { 
                        grid: { color: '#e9ecef' },
                        title: { display: false } 
                    }
                },
                interaction: {
                    mode: 'nearest',
                    intersect: false
                },
                plugins: {
                    legend: { position: 'top' },
                    zoom: {
                        pan: { enabled: true, mode: 'x' },
                        zoom: { wheel: { enabled: true }, mode: 'x' }
                    }
                }
            }
        });
    }

    function updateChart() {
        fetch('/sensors')
        .then(response => response.json())
        .then(data => {
            const tempData = data.map(d => ({x: d.time * 1000, y: d.temperature}));
            const humData = data.map(d => ({x: d.time * 1000, y: d.humidity}));

            sensorChart.data.datasets[0].data = tempData;
            sensorChart.data.datasets[1].data = humData;
            sensorChart.update('none');
        });
    }

    // Initialize chart on load
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
            <div class="metric">
                Temperature: <span id="temperature">-</span>
            </div>
            <div class="metric">
                Humidity: <span id="humidity">-</span>
            </div>
            <div class="metric red">
                Red Objects: <span id="red-count">0</span>
            </div>
        </div>

        <div class="dashboard">
            <div class="card">
                <div class="video-container">
                    <img src="stream.mjpg" width="100%" height="480" />
                </div>
            </div>
            
            <div class="card">
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