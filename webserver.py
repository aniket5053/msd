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

# Initialize sensor
sht = adafruit_sht4x.SHT4x(board.I2C())

# Initialize LEDs and LED flag
dots = dotstar.DotStar(board.SCK, board.MOSI, 4, brightness=0.2)
LED_enable = False
LED_enable_time = 0

# Global flag for streaming state
streaming = False

# Store sensor readings with thread safety
sensor_data = deque(maxlen=100)
data_lock = Lock()

# Updated HTML page with a toggle button for streaming
PAGE = """\
<html>
<head>
<title>HiveHealth Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Roboto+Condensed:wght@400;700&family=Honeybee&display=swap" rel="stylesheet">
<style>
    /* (existing CSS unchanged) */
    /* Add any extra styling for the toggle button if desired */
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

    // Toggle streaming button handler
    window.addEventListener('load', () => {
        initChart();
        setInterval(updateMetrics, 1000);
        setInterval(updateChart, 10000);
        document.getElementById('toggleStreaming').addEventListener('click', function() {
            fetch('/toggle_stream')
              .then(response => response.json())
              .then(data => {
                 if (data.streaming) {
                      document.getElementById('toggleStreaming').textContent = 'Stop Streaming';
                 } else {
                      document.getElementById('toggleStreaming').textContent = 'Start Streaming';
                 }
              });
        });
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
            <!-- Toggle button overlay -->
            <div class="toggle-button-container" style="position: absolute; top: 1rem; right: 1rem; z-index: 2;">
                <button id="toggleStreaming" style="padding: 0.5rem 1rem; font-size: 1rem;">Start Streaming</button>
            </div>
            <div class="status-bar">
                <div class="metric green">
                    <svg class="metric-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12,15A2,2 0 0,1 14,17A2,2 0 0,1 12,19A2,2 0 0,1 10,17A2,2 0 0,1 12,15M12,10A2,2 0 0,1 14,12A2,2 0 0,1 12,14A2,2 0 0,1 10,12A2,2 0 0,1 12,10M12,5A2,2 0 0,1 14,7A2,2 0 0,1 12,9A2,2 0 0,1 10,7A2,2 0 0,1 12,5M8.5,10A2.5,2.5 0 0,1 11,12.5A2.5,2.5 0 0,1 8.5,15A2.5,2.5 0 0,1 6,12.5A2.5,2.5 0 0,1 8.5,10M15.5,10A2.5,2.5 0 0,1 18,12.5A2.5,2.5 0 0,1 15.5,15A2.5,2.5 0 0,1 13,12.5A2.5,2.5 0 0,1 15.5,10M8.5,5A2.5,2.5 0 0,1 11,7.5A2.5,2.5 0 0,1 8.5,10A2.5,2.5 0 0,1 6,7.5A2.5,2.5 0 0,1 8.5,5M15.5,5A2.5,2.5 0 0,1 18,7.5A2.5,2.5 0 0,1 15.5,10A2.5,2.5 0 0,1 13,7.5A2.5,2.5 0 0,1 15.5,5M12,2C14.5,2 16.75,2.89 18.5,4.38C17.12,5.14 16,6.05 15,7L12,4L9,7C8,6.05 6.88,5.14 5.5,4.38C7.25,2.89 9.5,2 12,2M12,22C9.5,22 7.25,21.11 5.5,19.62C6.88,18.86 8,17.95 9,17L12,20L15,17C16,17.95 17.12,18.86 18.5,19.62C16.75,21.11 14.5,22 12,22Z"/>
                    </svg>
                    <span id="temp">-</span>F
                </div>
                <div class="metric blue">
                    <svg class="metric-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12,3.25C12,3.25 6,10 6,14C6,17.32 8.69,20 12,20A6,6 0 0,0 18,14C18,10 12,3.25 12,3.25M14.47,9.97L15.53,11.03L9.53,17.03L8.47,15.97M9.75,10A1.25,1.25 0 0,1 11,11.25A1.25,1.25 0 0,1 9.75,12.5A1.25,1.25 0 0,1 8.5,11.25A1.25,1.25 0 0,1 9.75,10M14.25,14.5A1.25,1.25 0 0,1 15.5,15.75A1.25,1.25 0 0,1 14.25,17A1.25,1.25 0 0,1 13,15.75A1.25,1.25 0 0,1 14.25,14.5Z"/>
                    </svg>
                    <span id="hum">-</span>%
                </div>
                <div class="metric red">
                    <svg class="metric-icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2M17,15V13H7V15L12,20L17,15M12,4A8,8 0 0,1 20,12A8,8 0 0,1 12,20A8,8 0 0,1 4,12A8,8 0 0,1 12,4M10,7H14V9H10V7M10,11H14V13H10V11Z"/>
                    </svg>
                    <span id="red-count">0</span>
                </div>
            </div>
            <img class="video-feed" src="stream.mjpg" />
        </div>
        
        <div class="graph-section">
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
        global streaming, LED_enable, LED_enable_time, picam2
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
            # If streaming is off, return a message instead of the stream
            if not streaming:
                self.send_response(503)
                self.send_header('Content-Type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'Streaming is turned off.')
                return
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
        elif self.path == '/toggle_stream':
            # Toggle streaming on/off
            global picam2
            if streaming:
                picam2.stop_recording()
                streaming_state = False
                # Turn off LED immediately when streaming stops
                LED_enable = False
                for i in range(4):
                    dots[i] = (0, 0, 0)
            else:
                picam2.start_recording(JpegEncoder(), FileOutput(output))
                streaming_state = True
                LED_enable = True
                LED_enable_time = time.time()
            # Update our global flag
            streaming = streaming_state
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'streaming': streaming}).encode('utf-8'))
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
            temp = temp * 9/5 + 32  # Convert to Fahrenheit
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

def led_monitor_loop():
    global LED_enable, LED_enable_time, streaming
    while True:
        # If streaming is active and LED is on, check if 60 seconds have elapsed.
        if streaming and LED_enable and (time.time() - LED_enable_time > 60):
            LED_enable = False
            for i in range(4):
                dots[i] = (0, 0, 0)
        time.sleep(1)

# Initialize camera with square aspect ratio
picam2 = Picamera2()
config = picam2.create_video_configuration({
    'size': (640, 640),  # Square resolution
    'format': 'XRGB8888'
})
picam2.configure(config)
output = StreamingOutput()
# Note: we no longer start recording automatically; we wait for the toggle

# Start sensor thread
sensor_thread = threading.Thread(target=sensor_loop, daemon=True)
sensor_thread.start()

# Start LED monitor thread
led_thread = threading.Thread(target=led_monitor_loop, daemon=True)
led_thread.start()

try:
    address = ('', 7123)
    server = StreamingServer(address, StreamingHandler)
    server.serve_forever()
finally:
    if streaming:
        picam2.stop_recording()
