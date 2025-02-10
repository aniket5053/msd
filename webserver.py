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
<title>BeeCam - Environmental Monitoring</title>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap" rel="stylesheet">
<style>
    html, body {
        margin: 10px;
        padding: 0;
        height: calc(100% - 20px);
        font-family: 'Roboto', sans-serif;
        background-color: #fafafa;
    }
    .container {
        display: flex;
        flex-direction: column;
        height: 100%;
        border: 2px solid #e0e0e0;
        border-radius: 10px;
        background: white;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    .dashboard {
        display: grid;
        grid-template-columns: 3fr 2fr;
        height: calc(100% - 80px);
        gap: 15px;
        padding: 15px;
    }
    h1 {
        color: #2c3e50;
        margin: 15px;
        font-size: 2em;
        padding-bottom: 15px;
        border-bottom: 2px solid #eee;
    }
    .status-bar {
        display: flex;
        gap: 15px;
        padding: 0 15px;
    }
    .metric {
        background: rgba(0, 0, 0, 0.05);
        padding: 10px 20px;
        border-radius: 8px;
        color: #333;
        font-size: 1.1em;
        border: 1px solid #eee;
    }
    .metric.red { color: #d32f2f; border-color: #ffcdd2; }
    .metric.green { color: #388e3c; border-color: #c8e6c9; }
    .metric.blue { color: #1976d2; border-color: #bbdefb; }
    #sensorChart {
        width: 100% !important;
        height: 100% !important;
        min-width: 500px;
        background: white;
        border-radius: 8px;
        border: 1px solid #eee;
        padding: 15px;
    }
    .chartjs-tooltip {
        background: rgba(50, 50, 50, 0.9) !important;
        color: white !important;
        border-radius: 4px !important;
        padding: 8px 12px !important;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }
    .video-container {
        background: #f5f5f5;
        border-radius: 8px;
        overflow: hidden;
        height: 100%;
        border: 1px solid #eee;
        position: relative;
        min-width: 600px;
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
                    borderColor: '#d32f2f',
                    backgroundColor: '#d32f2f22',
                    tension: 0.2
                },{
                    label: 'Humidity (%)',
                    borderColor: '#1976d2',
                    backgroundColor: '#1976d222',
                    tension: 0.2
                }]
            },
            options: {
                maintainAspectRatio: false,
                layout: {
                    padding: {
                        left: 20,
                        right: 20,
                        top: 15,
                        bottom: 25
                    }
                },
                interaction: {
                    mode: 'nearest',
                    intersect: false
                },
                plugins: {
                    tooltip: {
                        backgroundColor: 'rgba(40, 40, 40, 0.95)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: 'rgba(255,255,255,0.2)',
                        borderWidth: 1,
                        padding: 12,
                        callbacks: {
                            title: (context) => {
                                const date = new Date(context[0].parsed.x);
                                return date.toLocaleTimeString();
                            },
                            label: (context) => {
                                const label = context.dataset.label || '';
                                return `${label}: ${context.parsed.y.toFixed(1)}`;
                            },
                            footer: (context) => {
                                const index = context[0].dataIndex;
                                return `Data point: ${index + 1}`;
                            }
                        }
                    },
                    legend: { labels: { color: '#333' } },
                    zoom: {
                        pan: { enabled: true, mode: 'x' },
                        zoom: { wheel: { enabled: true }, mode: 'x' }
                    }
                },
                scales: {
                    x: {
                        type: 'time',
                        time: {
                            displayFormats: {
                                minute: 'HH:mm',
                                hour: 'HH:mm'
                            },
                            tooltipFormat: 'HH:mm:ss'
                        },
                        ticks: {
                            source: 'data',
                            autoSkip: false,
                            color: '#444',
                            font: { size: 12 },
                            callback: (value, index, ticks) => {
                                return (index % 5 === 0 || index === ticks.length - 1) ? 
                                    new Date(value).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) : 
                                    '';
                            }
                        },
                        grid: { 
                            color: '#eee',
                            drawTicks: false,
                            drawBorder: true
                        },
                        bounds: 'ticks',
                        title: {
                            display: true,
                            text: 'Time',
                            color: '#666',
                            font: { size: 14 }
                        }
                    },
                    y: {
                        grid: { 
                            color: '#eee',
                            drawTicks: false,
                            drawBorder: true
                        },
                        ticks: { 
                            color: '#444',
                            font: { size: 12 }
                        },
                        title: {
                            display: true,
                            text: 'Value',
                            color: '#666',
                            font: { size: 14 }
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

    window.addEventListener('load', () => {
        initChart();
        setInterval(updateMetrics, 1000);
        setInterval(updateChart, 10000);
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