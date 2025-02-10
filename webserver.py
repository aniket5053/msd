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
    /* ... (keep existing styles the same) ... */
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
                    borderColor: '#d32f2f',
                    backgroundColor: '#d32f2f22',
                    tension: 0.2,
                    pointRadius: 3,
                    pointHoverRadius: 6,
                    pointBackgroundColor: '#d32f2f'
                },{
                    label: 'Humidity (%)',
                    borderColor: '#1976d2',
                    backgroundColor: '#1976d222',
                    tension: 0.2,
                    pointRadius: 3,
                    pointHoverRadius: 6,
                    pointBackgroundColor: '#1976d2'
                }]
            },
            options: {
                maintainAspectRatio: false,
                interaction: {
                    mode: 'nearest',
                    intersect: false
                },
                scales: {
                    x: {
                        type: 'time',
                        time: {
                            unit: 'minute',
                            displayFormats: {
                                minute: 'HH:mm',
                                hour: 'HH:mm'
                            },
                            tooltipFormat: 'HH:mm:ss'
                        },
                        grid: { color: '#f5f5f5' },
                        ticks: {
                            color: '#666',
                            maxRotation: 0,
                            autoSkip: true,
                            maxTicksLimit: 10
                        }
                    },
                    y: {
                        grid: { color: '#f5f5f5' },
                        ticks: { color: '#666' }
                    }
                },
                plugins: {
                    legend: { labels: { color: '#333' } },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        displayColors: true,
                        callbacks: {
                            title: (context) => {
                                const date = new Date(context[0].parsed.x);
                                return date.toLocaleTimeString();
                            },
                            label: (context) => {
                                const label = context.dataset.label;
                                const value = context.parsed.y;
                                return `${label}: ${value.toFixed(1)}`;
                            }
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
            sensorChart.data.datasets[0].data = data.map(d => ({
                x: d.time * 1000, 
                y: d.temperature
            }));
            sensorChart.data.datasets[1].data = data.map(d => ({
                x: d.time * 1000, 
                y: d.humidity
            }));
            
            // Only keep latest 50 points for better visibility
            if(data.length > 50) {
                sensorChart.data.datasets.forEach(dataset => {
                    dataset.data = dataset.data.slice(-50);
                });
            }
            
            sensorChart.update();
        });
    }

    window.addEventListener('load', () => {
        initChart();
        setInterval(updateMetrics, 10000);
        setInterval(updateChart, 10000);
    });
</script>
</head>
<body>
    <!-- Keep existing HTML structure -->
</body>
</html>
"""

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()
        self.red_count = 0

    def write(self, buf):
        # Color detection processing
        img = cv2.imdecode(np.frombuffer(buf, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            
            # Red color range
            lower_red = np.array([0, 120, 70])
            upper_red = np.array([10, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red, upper_red)
            
            lower_red = np.array([170, 120, 70])
            upper_red = np.array([180, 255, 255])
            mask2 = cv2.inRange(hsv, lower_red, upper_red)
            
            full_mask = cv2.bitwise_or(mask1, mask2)
            contours, _ = cv2.findContours(full_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            
            self.red_count = len([c for c in contours if cv2.contourArea(c) > 50])
            print(self.red_count)

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
        time.sleep(10)  # Update every 10 seconds

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