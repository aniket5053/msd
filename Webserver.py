import io
import logging
import socketserver
import json
import time
from collections import deque
from http import server
from threading import Condition
import cv2
import numpy as np
import board
import adafruit_sht4x

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

# Initialize sensor
sht = adafruit_sht4x.SHT4x(board.I2C())

# Store last 100 sensor readings
sensor_data = deque(maxlen=100)

PAGE = """\
<html>
<head>
<title>BeeCam - Picamera2 MJPEG Streaming</title>
<style>
    body {
        background-color: #FFF8E1;
        font-family: 'Arial', sans-serif;
        text-align: center;
        color: #3E2723;
    }
    h1 {
        font-size: 3em;
        color: #F57F17;
        font-family: 'Comic Sans MS', sans-serif;
    }
    #red-count {
        font-size: 1.5em;
        margin-top: 20px;
        font-weight: bold;
        color: #F44336;
    }
    canvas {
        max-width: 90%;
        margin-top: 20px;
    }
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script type="text/javascript">
    let sensorChart;
    let labels = [];
    let temperatureData = [];
    let humidityData = [];

    function updateRedCount() {
        fetch('/count')
        .then(response => response.json())
        .then(data => {
            document.getElementById('red-count').innerText = 'Red Objects Detected: ' + data.count;
        });
    }

    function updateGraph() {
        fetch('/sensors')
        .then(response => response.json())
        .then(data => {
            labels = data.map(d => new Date(d.time * 1000).toLocaleTimeString());
            temperatureData = data.map(d => d.temperature);
            humidityData = data.map(d => d.humidity);

            if (!sensorChart) {
                const ctx = document.getElementById('sensorChart').getContext('2d');
                sensorChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: 'Temperature (°C)',
                                data: temperatureData,
                                borderColor: 'red',
                                fill: false
                            },
                            {
                                label: 'Humidity (%)',
                                data: humidityData,
                                borderColor: 'blue',
                                fill: false
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        scales: {
                            x: { title: { display: true, text: 'Time' } },
                            y: { title: { display: true, text: 'Value' } }
                        }
                    }
                });
            } else {
                sensorChart.data.labels = labels;
                sensorChart.data.datasets[0].data = temperatureData;
                sensorChart.data.datasets[1].data = humidityData;
                sensorChart.update();
            }
        });
    }

    setInterval(updateRedCount, 500);
    setInterval(updateGraph, 2000);
</script>
</head>
<body>
    <h1>BeeCam - Live Stream</h1>
    <p id="red-count">Red Objects Detected: 0</p>
    <canvas id="sensorChart"></canvas>
    <img src="stream.mjpg" width="640" height="480" />
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
    is_streaming = False  

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
            if not StreamingHandler.is_streaming:
                StreamingHandler.is_streaming = True
                print("Streaming started...")
                self.send_response(200)
                self.send_header('Age', 0)
                self.send_header('Cache-Control', 'no-cache, private')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
                self.end_headers()

                try:
                    while StreamingHandler.is_streaming:
                        with output.condition:
                            output.condition.wait()
                            frame = output.frame

                        np_frame = np.frombuffer(frame, dtype=np.uint8)
                        img = cv2.imdecode(np_frame, cv2.IMREAD_COLOR)
                        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

                        lower_red1 = np.array([0, 120, 70])
                        upper_red1 = np.array([10, 255, 255])
                        mask1 = cv2.inRange(hsv, lower_red1, upper_red1)

                        lower_red2 = np.array([170, 120, 70])
                        upper_red2 = np.array([180, 255, 255])
                        mask2 = cv2.inRange(hsv, lower_red2, upper_red2)

                        mask = mask1 | mask2
                        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                        red_count = sum(1 for contour in contours if cv2.contourArea(contour) > 500)
                        output.set_red_count(red_count)

                        _, encoded_frame = cv2.imencode('.jpg', img)
                        frame = encoded_frame.tobytes()

                        self.wfile.write(b'--FRAME\r\n')
                        self.send_header('Content-Type', 'image/jpeg')
                        self.send_header('Content-Length', len(frame))
                        self.end_headers()
                        self.wfile.write(frame)
                        self.wfile.write(b'\r\n')
                except Exception as e:
                    logging.warning('Removed streaming client %s: %s', self.client_address, str(e))
                finally:
                    StreamingHandler.is_streaming = False
                    print("Streaming stopped...")
            else:
                self.send_error(404)
                self.end_headers()
        elif self.path == '/count':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            count_data = {'count': output.get_red_count()}
            self.wfile.write(json.dumps(count_data).encode('utf-8'))
        elif self.path == '/sensors':
            temp = round(sht.temperature, 3)
            hum = round(sht.relative_humidity, 3)
            sensor_data.append({"time": time.time(), "temperature": temp, "humidity": hum})

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(list(sensor_data)).encode('utf-8'))
        else:
            self.send_error(404)
            self.end_headers()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
output = StreamingOutput()
picam2.start_recording(JpegEncoder(), FileOutput(output))

try:
    address = ('', 7123)
    server = StreamingServer(address, StreamingHandler)
    server.serve_forever()
finally:
    picam2.stop_recording()
